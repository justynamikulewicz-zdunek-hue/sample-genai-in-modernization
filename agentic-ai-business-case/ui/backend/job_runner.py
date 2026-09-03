"""
Generation job — runs as a one-shot Fargate task, not in the web process.

The web tier is a Lambda with a hard 900-second ceiling that cannot be raised,
while a full business case can legitimately run far longer on a large RVTools
export. Rather than splitting the agent graph into per-agent functions (it
already orchestrates its own phases), the whole generation is handed to ECS
RunTask and the Lambda just reports progress.

Invoked by RunTask with a command override. Input arrives via S3 because the
browser uploads straight there; output goes back to S3 and the case row in
DynamoDB is updated so /api/status can see it.

Reuses app.py's S3 and DynamoDB helpers rather than restating them. Importing
app.py builds the Flask object without serving anything, which is harmless here
and keeps a single definition of how files are named and where they land.
"""
import os
import sys
import json
import glob
import traceback
from datetime import datetime

# RunTask starts us with WORKDIR /app, but the backend modules resolve relative
# to their own directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import (  # noqa: E402
    INPUT_DIR,
    OUTPUT_DIR,
    S3_INPUT_BUCKET,
    S3_OUTPUT_BUCKET,
    s3_client,
    dynamodb_table,
    is_s3_enabled,
    is_dynamodb_enabled,
    safe_case_id,
    safe_path,
    upload_file_to_s3,
    run_business_case_generator,
)


def _update_status(case_id, created_at, status, **extra):
    """Record progress on the existing case row so /api/status can read it.

    Best-effort: a failure to write status must not mask the real error from
    the generation itself.
    """
    if not is_dynamodb_enabled():
        return
    try:
        names = {'#s': 'status'}  # 'status' is a DynamoDB reserved word
        values = {':s': status, ':u': datetime.utcnow().isoformat()}
        sets = ['#s = :s', 'lastUpdated = :u']
        for i, (key, value) in enumerate(extra.items()):
            names[f'#k{i}'] = key
            values[f':v{i}'] = value
            sets.append(f'#k{i} = :v{i}')

        dynamodb_table.update_item(
            Key={'caseId': case_id, 'createdAt': created_at},
            UpdateExpression='SET ' + ', '.join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except Exception as e:
        print(f"Warning: could not update status to {status}: {e}")


def _download_inputs(case_id):
    """Pull this case's uploaded files out of S3 into the local input tree.

    The agents read from disk, and the browser uploaded straight to S3 (files
    routinely exceed Lambda's 6 MB request limit), so the job has to stage them.
    """
    case_input_dir = safe_path(INPUT_DIR, case_id)
    os.makedirs(case_input_dir, exist_ok=True)

    paginator = s3_client.get_paginator('list_objects_v2')
    count = 0
    for page in paginator.paginate(Bucket=S3_INPUT_BUCKET, Prefix=f"{case_id}/"):
        for obj in page.get('Contents', []):
            filename = obj['Key'].split('/', 1)[1]
            if not filename:
                continue  # the prefix placeholder itself
            local_path = safe_path(case_input_dir, filename)
            s3_client.download_file(S3_INPUT_BUCKET, obj['Key'], local_path)
            count += 1
            print(f"  ← {obj['Key']}")

    # The agents also look for project_info.json in the base input directory to
    # discover which case they are working on.
    case_project_info = os.path.join(case_input_dir, 'project_info.json')
    if os.path.exists(case_project_info):
        with open(case_project_info, encoding='utf-8') as f:
            info = json.load(f)
        with open(os.path.join(INPUT_DIR, 'project_info.json'), 'w', encoding='utf-8') as f:
            json.dump(info, f, indent=2)
        return info

    raise RuntimeError(f"project_info.json missing for case {case_id}")


def _collect_outputs(case_id):
    """Gather everything the generator produced and push it back to S3.

    Moved here from the old synchronous /api/generate handler — the web tier no
    longer sees these files at all.
    """
    case_output_dir = safe_path(OUTPUT_DIR, case_id)

    output_file = safe_path(case_output_dir, 'aws_business_case.md')
    if not os.path.exists(output_file):
        output_file = os.path.join(OUTPUT_DIR, 'aws_business_case.md')
    if not os.path.exists(output_file):
        raise RuntimeError('Business case file not generated')

    with open(output_file, encoding='utf-8') as f:
        content = f.read()

    output_s3_keys = {}
    if is_s3_enabled():
        key = upload_file_to_s3(output_file, case_id, 'aws_business_case.md')
        if key:
            output_s3_keys['business_case'] = key

        # Fixed-name Excel exports: prefer the per-case folder, fall back to root.
        for label, filename in (
            ('excel_mapping', 'vm_to_ec2_mapping.xlsx'),
            ('eks_analysis', 'eks_migration_analysis.xlsx'),
        ):
            path = safe_path(case_output_dir, filename)
            if not os.path.exists(path):
                path = safe_path(OUTPUT_DIR, filename)
            if os.path.exists(path):
                key = upload_file_to_s3(path, case_id, filename)
                if key:
                    output_s3_keys[label] = key

        # IT inventory export carries a timestamp in its name; take the newest.
        matches = glob.glob(os.path.join(case_output_dir, 'it_inventory_aws_pricing_*.xlsx')) \
            or glob.glob(os.path.join(OUTPUT_DIR, 'it_inventory_aws_pricing_*.xlsx'))
        if matches:
            newest = max(matches, key=os.path.getmtime)
            key = upload_file_to_s3(newest, case_id, os.path.basename(newest))
            if key:
                output_s3_keys['it_inventory'] = key

    return content, output_s3_keys


def main():
    case_id = os.environ.get('CASE_ID')
    created_at = os.environ.get('CASE_CREATED_AT')
    if not case_id or not created_at:
        print("FATAL: CASE_ID and CASE_CREATED_AT must be set by RunTask")
        return 2

    case_id = safe_case_id(case_id)
    print(f"=== Generation job for {case_id} ===")

    try:
        _update_status(case_id, created_at, 'RUNNING')

        print("Staging inputs from S3...")
        project_info = _download_inputs(case_id)
        selected_agents = project_info.get('selectedAgents', [])

        print("Running generator...")
        result = run_business_case_generator(project_info, selected_agents)

        print("Collecting outputs...")
        content, output_s3_keys = _collect_outputs(case_id)

        _update_status(
            case_id, created_at, 'COMPLETED',
            businessCaseContent=content,
            outputS3Keys=output_s3_keys,
            executionStats={
                'agentsExecuted': len(selected_agents),
                'executionTime': result.get('execution_time', 'N/A'),
                'tokenUsage': result.get('token_usage', 'N/A'),
            },
        )
        print(f"=== Done: {case_id} ===")
        return 0

    except Exception as e:
        traceback.print_exc()
        # Truncated so a huge traceback cannot blow the DynamoDB item size limit.
        _update_status(case_id, created_at, 'FAILED', errorMessage=str(e)[:2000])
        return 1


if __name__ == '__main__':
    sys.exit(main())
