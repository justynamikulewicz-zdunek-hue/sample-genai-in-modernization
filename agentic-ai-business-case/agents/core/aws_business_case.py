import os
from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent import GraphBuilder
from strands.multiagent.graph import GraphState
from strands.multiagent.base import Status

from agents.config.config import model_id_claude3_7, model_temperature, output_folder_dir_path, ENABLE_MULTI_STAGE, MAX_TOKENS_BUSINESS_CASE, EKS_CONFIG, BEDROCK_RETRY_CONFIG
from agents.analysis.inventory_analysis import it_analysis, calculate_it_inventory_arr, extract_atx_arr_tool
from agents.analysis.rv_tool_analysis import rv_tool_analysis
from agents.analysis.atx_analysis import read_excel_file, read_pdf_file, read_pptx_file
from agents.analysis.mra_analysis import read_docx_file, read_markdown_file, read_pdf_file
from agents.strategy.migration_strategy import read_migration_strategy_framework, read_portfolio_assessment
from agents.strategy.migration_plan import read_migration_plan_framework
from agents.strategy.wave_planning import generate_wave_plan_from_dependencies
from agents.pricing.pricing_tools import calculate_exact_aws_arr, compare_pricing_models, get_vm_cost_breakdown
from agents.utils.project_context import get_project_context, get_project_info_dict
from agents.utils.setup_logging import setup_logging
from agents.core.multi_stage_business_case import generate_multi_stage_business_case
from agents.export.appendix_content import get_appendix
# EKS imports
from agents.pricing.eks_pricing import (
    categorize_vms_for_eks,
    calculate_eks_cluster_size,
    calculate_eks_costs_async,
    allocate_eks_costs_to_vms,
    recommend_eks_strategy
)
from agents.analysis.mra_analysis import parse_mra_for_eks
from agents.export.excel_export import export_eks_analysis_to_excel
import concurrent.futures
from agents.prompt_library.agent_prompts import (
    system_message_aws_arr_cost, 
    system_message_rv_tool_analysis, 
    system_message_it_analysis,
    system_message_aws_business_case,
    system_message_current_state_analysis,
    system_message_atx_analysis,
    system_message_mra_analysis,
    system_message_migration_strategy,
    system_message_migration_plan )

# Apply configuration overrides before any processing
try:
    from agents.config.config_manager import apply_overrides_to_config
    apply_overrides_to_config()
    logger_temp = setup_logging()[0]
    logger_temp.info("✓ Configuration overrides applied")
except Exception as e:
    logger_temp = setup_logging()[0]
    logger_temp.warning(f"Could not apply config overrides: {e}")

# Setup logging
logger, log_file = setup_logging()
logger.info("="*80)
logger.info("AWS BUSINESS CASE GENERATOR - STARTING")
logger.info("="*80)

# Create a BedrockModel with max_tokens limit to prevent overflow
# Add slight temperature variation to break caching
import random
temp_variation = model_temperature + (random.random() * 0.02 - 0.01)  # ±0.01 variation
bedrock_model = BedrockModel(
    model_id=model_id_claude3_7,
    temperature=temp_variation,  # Slight variation to break cache
    max_tokens=8192,  # Balanced: prevents MaxTokensReachedException while limiting verbosity for speed
    boto_client_config=BEDROCK_RETRY_CONFIG  # Add retry configuration for production
)

# Create model for cost calculations with lower temperature for consistency
# Cost agent calls multiple tools (pricing calculator, comparison) which build up conversation history.
# Needs higher max_tokens to fit final response after tool results accumulate.
bedrock_model_cost = BedrockModel(
    model_id=model_id_claude3_7,
    temperature=0.1,  # Lower temperature for more deterministic cost calculations
    max_tokens=8192,  # Needs room for response after multiple tool call results
    boto_client_config=BEDROCK_RETRY_CONFIG  # Add retry configuration for production
)

# Create separate model for business case with higher max tokens
bedrock_model_business_case = BedrockModel(
    model_id=model_id_claude3_7,
    temperature=model_temperature,
    max_tokens=8192,  # Balanced for business case generation speed
    boto_client_config=BEDROCK_RETRY_CONFIG  # Add retry configuration for production
)

agent_it_analysis = Agent(model=bedrock_model,system_prompt= system_message_it_analysis,tools=[it_analysis], callback_handler=None)
agent_rv_tool_analysis = Agent(model=bedrock_model,system_prompt= system_message_rv_tool_analysis,tools=[rv_tool_analysis], callback_handler=None)
agent_atx_analysis = Agent(model=bedrock_model,system_prompt= system_message_atx_analysis,tools=[read_excel_file, read_pdf_file, read_pptx_file], callback_handler=None)
agent_mra_analysis = Agent(model=bedrock_model,system_prompt= system_message_mra_analysis,tools=[read_docx_file, read_markdown_file, read_pdf_file], callback_handler=None)
agent_migration_strategy = Agent(model=bedrock_model,system_prompt= system_message_migration_strategy,tools=[read_migration_strategy_framework, read_portfolio_assessment], callback_handler=None)
agent_migration_plan = Agent(model=bedrock_model,system_prompt= system_message_migration_plan,tools=[read_migration_plan_framework, generate_wave_plan_from_dependencies], callback_handler=None)
agent_aws_cost_arr = Agent(model=bedrock_model_cost,system_prompt= system_message_aws_arr_cost,tools=[it_analysis,rv_tool_analysis,calculate_exact_aws_arr,compare_pricing_models,get_vm_cost_breakdown,calculate_it_inventory_arr,extract_atx_arr_tool], callback_handler=None)  # Use lower temperature for deterministic costs with pricing tools and comparison
current_state_analysis = Agent(model=bedrock_model,system_prompt= system_message_current_state_analysis,tools=[it_analysis,rv_tool_analysis], callback_handler=None)
aws_business_case = Agent(model=bedrock_model_business_case,system_prompt= system_message_aws_business_case, callback_handler=None)  # Use higher token limit


# Define conditional edge functions using the factory pattern
def all_dependencies_complete(required_nodes: list[str]):
    """Factory function to create AND condition for multiple dependencies."""
    def check_all_complete(state: GraphState) -> bool:
        return all(
            node_id in state.results and state.results[node_id].status == Status.COMPLETED
            for node_id in required_nodes
        )
    return check_all_complete

# Get project context early to determine input files
project_context = get_project_context()
project_info = get_project_info_dict()

# ================================================================================
# INITIALIZE CASE OUTPUT MANAGER EARLY
# ================================================================================
# Initialize case manager at the start to save files directly to case folder
# This prevents files from accumulating in root output folder
try:
    from agents.core.case_output_manager import get_case_output_manager
    case_manager = get_case_output_manager()
    logger.info(f"✓ Case ID: {case_manager.case_id}")
    logger.info(f"✓ Output Directory: {case_manager.case_output_dir}")
    
    # Override output_folder_dir_path to use case-specific folder
    output_folder_dir_path = case_manager.case_output_dir
    logger.info(f"✓ All outputs will be saved to: {output_folder_dir_path}")
except Exception as e:
    logger.warning(f"Could not initialize case manager: {e}")
    logger.warning("Files will be saved to root output folder")
    case_manager = None

# Get uploaded filenames from project_info if available, otherwise use patterns
uploaded_files = project_info.get('uploadedFiles', {})
case_id = project_info.get('caseId', '')

# Use case-specific paths if case ID exists
if case_id:
    input_base = f"input/{case_id}/"
    logger.info(f"Using case-specific input directory: {input_base}")
else:
    input_base = "input/"
    logger.info("Using base input directory (no case ID)")

input_files1 = f"{input_base}{uploaded_files.get('itInventory', 'it-infrastructure-inventory.xlsx')}" if 'itInventory' in uploaded_files else f"{input_base}it-infrastructure-inventory.xlsx"
input_files2 = f"{input_base}{uploaded_files['rvTool'][0]}" if 'rvTool' in uploaded_files and uploaded_files['rvTool'] else f"{input_base}rvtool*.xlsx"
input_files3_pptx = f"{input_base}{uploaded_files.get('atxPptx', 'atx_business_case.pptx')}" if 'atxPptx' in uploaded_files else f"{input_base}atx_business_case.pptx"

# SMART AGENT SELECTION - Only add agents for files that exist
logger.info("="*80)
logger.info("SMART AGENT SELECTION - Detecting available input files")
logger.info("="*80)

import glob

# Detect which input files are available
# For RVTools, check the actual uploaded file (not glob pattern)
# Use the configured input folder path (handles both dev and production)
from agents.config.config import input_folder_dir_path

rvtools_file_path = os.path.join(input_folder_dir_path, case_id, uploaded_files['rvTool'][0]) if case_id and 'rvTool' in uploaded_files and uploaded_files['rvTool'] else None

# Build absolute paths for all files using configured input folder
abs_input_files1 = os.path.join(input_folder_dir_path, input_files1)
abs_input_files3_pptx = os.path.join(input_folder_dir_path, input_files3_pptx)

has_it_inventory = os.path.exists(abs_input_files1)
has_rvtools = rvtools_file_path is not None and os.path.exists(rvtools_file_path)
has_atx_pptx = os.path.exists(abs_input_files3_pptx)
has_atx = has_atx_pptx



# Pre-read MRA file if it exists
mra_content = None
mra_status = "Not Available"
try:
    from agents.analysis.mra_analysis import find_mra_file, read_pdf_file as read_mra_pdf, read_docx_file, read_markdown_file
    
    mra_file = find_mra_file()
    if mra_file:
        logger.info(f"MRA file found: {mra_file}")
        
        # Read the file based on extension
        if mra_file.endswith('.pdf'):
            mra_content = read_mra_pdf(mra_file)
        elif mra_file.endswith('.docx') or mra_file.endswith('.doc'):
            mra_content = read_docx_file(mra_file)
        elif mra_file.endswith('.md'):
            mra_content = read_markdown_file(mra_file)
        
        if mra_content and len(mra_content) > 1000:
            mra_status = "Available"
            logger.info(f"MRA content loaded: {len(mra_content)} characters")
        else:
            logger.warning(f"MRA file found but content is minimal: {len(mra_content) if mra_content else 0} characters")
    else:
        logger.info("No MRA file found in input directory")
except Exception as e:
    logger.error(f"Error reading MRA file: {e}")
    import traceback
    logger.error(traceback.format_exc())

has_mra = mra_content is not None and len(mra_content) > 1000

logger.info(f"IT Inventory: {'✓ FOUND' if has_it_inventory else '✗ Not found'} - {input_files1}")
logger.info(f"RVTools: {'✓ FOUND' if has_rvtools else '✗ Not found'} - {input_files2}")
logger.info(f"ATX PowerPoint: {'✓ FOUND' if has_atx_pptx else '✗ Not found'} - {input_files3_pptx}")
logger.info(f"MRA: {'✓ FOUND' if has_mra else '✗ Not found'}")

# ============================================================================
# DETERMINISTIC PRICING CALCULATION (Pre-graph, pure Python)
# ============================================================================
# Generate pricing Excel BEFORE the graph runs. This ensures the cost analysis
# table is always available regardless of whether the LLM cost agent completes.
# The pricing tools are pure Python (AWS Price List API) - no LLM involved.
logger.info("="*80)
logger.info("DETERMINISTIC PRICING CALCULATION")
logger.info("="*80)

_pricing_generated = False
try:
    target_region = project_info.get('awsRegion', 'us-east-1')
    
    if has_rvtools and rvtools_file_path:
        from agents.pricing.pricing_tools import calculate_exact_aws_arr
        rvtools_basename = os.path.basename(rvtools_file_path)
        logger.info(f"Calculating pricing from RVTools: {rvtools_basename} (region: {target_region})")
        _pricing_result = calculate_exact_aws_arr(rvtools_filename=rvtools_basename, target_region=target_region)
        if _pricing_result:
            _pricing_generated = True
            logger.info("✓ Deterministic pricing Excel generated (RVTools)")
    elif has_it_inventory:
        from agents.analysis.inventory_analysis import calculate_it_inventory_arr
        it_basename = os.path.basename(input_files1)
        logger.info(f"Calculating pricing from IT Inventory: {it_basename} (region: {target_region})")
        _pricing_result = calculate_it_inventory_arr(inventory_filename=it_basename, target_region=target_region)
        if _pricing_result:
            _pricing_generated = True
            logger.info("✓ Deterministic pricing Excel generated (IT Inventory)")
    elif has_atx_pptx and atx_summary:
        logger.info("ATX PowerPoint pricing - using pre-computed summary (no Excel generation needed)")
        _pricing_generated = True
    else:
        logger.warning("⚠ No input data available for pricing calculation")
except Exception as _pricing_err:
    logger.error(f"Deterministic pricing calculation failed: {_pricing_err}")
    import traceback
    logger.error(traceback.format_exc())

if not _pricing_generated:
    logger.warning("⚠ Pricing Excel could not be generated - Cost Analysis section will be unavailable")
logger.info("="*80)

# Track which analysis agents will be added
active_analysis_agents = []

# Build the graph
builder = GraphBuilder()

# Add analysis agents ONLY if their input files exist
if has_it_inventory:
    builder.add_node(agent_it_analysis, "agent_it_analysis")
    active_analysis_agents.append("agent_it_analysis")
    logger.info("→ Adding agent_it_analysis to graph")

if has_rvtools:
    builder.add_node(agent_rv_tool_analysis, "agent_rv_tool_analysis")
    active_analysis_agents.append("agent_rv_tool_analysis")
    logger.info("→ Adding agent_rv_tool_analysis to graph")

if has_atx:
    builder.add_node(agent_atx_analysis, "agent_atx_analysis")
    active_analysis_agents.append("agent_atx_analysis")
    logger.info("→ Adding agent_atx_analysis to graph")

if has_mra:
    builder.add_node(agent_mra_analysis, "agent_mra_analysis")
    active_analysis_agents.append("agent_mra_analysis")
    logger.info("→ Adding agent_mra_analysis to graph")

# Always add synthesis and planning agents
builder.add_node(current_state_analysis, "current_state_analysis")
# NOTE: agent_aws_cost_arr REMOVED from graph - pricing is now calculated deterministically
# before the graph runs (pure Python, no LLM). This eliminates MaxTokensReachedException
# and speeds up execution by ~2-3 minutes.
builder.add_node(agent_migration_strategy, "agent_migration_strategy")
builder.add_node(agent_migration_plan, "agent_migration_plan")

# Only add aws_business_case node if NOT using multi-stage generation
if not ENABLE_MULTI_STAGE:
    builder.add_node(aws_business_case, "aws_business_case")

logger.info(f"Active analysis agents: {active_analysis_agents}")
logger.info("="*80)

# Build edges dynamically based on active agents
# (1) current_state_analysis executes ONLY when ALL active analysis agents complete
if active_analysis_agents:
    condition_for_current_state = all_dependencies_complete(active_analysis_agents)
    for agent_id in active_analysis_agents:
        builder.add_edge(agent_id, "current_state_analysis", condition=condition_for_current_state)

# (2) agent_migration_strategy executes ONLY when ALL active analysis agents complete
if active_analysis_agents:
    condition_for_migration_strategy = all_dependencies_complete(active_analysis_agents)
    for agent_id in active_analysis_agents:
        builder.add_edge(agent_id, "agent_migration_strategy", condition=condition_for_migration_strategy)

# (3) agent_migration_plan executes ONLY when current_state_analysis AND agent_migration_strategy complete
condition_for_migration_plan = all_dependencies_complete(["current_state_analysis", "agent_migration_strategy"])
builder.add_edge("current_state_analysis", "agent_migration_plan", condition=condition_for_migration_plan)
builder.add_edge("agent_migration_strategy", "agent_migration_plan", condition=condition_for_migration_plan)

# (4) aws_business_case executes ONLY when ALL intermediate agents complete
# Skip if multi-stage is enabled (we'll generate business case separately)
if not ENABLE_MULTI_STAGE:
    condition_for_business_case = all_dependencies_complete(["current_state_analysis", "agent_migration_strategy", "agent_migration_plan"])
    builder.add_edge("current_state_analysis", "aws_business_case", condition=condition_for_business_case)
    builder.add_edge("agent_migration_strategy", "aws_business_case", condition=condition_for_business_case)
    builder.add_edge("agent_migration_plan", "aws_business_case", condition=condition_for_business_case)


# Set entry points (the nodes that start first - they run in parallel)
# Only set entry points for active analysis agents
for agent_id in active_analysis_agents:
    builder.set_entry_point(agent_id)
    logger.info(f"Set entry point: {agent_id}")

logger.info(f"Multi-stage generation: {'ENABLED' if ENABLE_MULTI_STAGE else 'DISABLED'}")
if ENABLE_MULTI_STAGE:
    logger.info("Single-stage aws_business_case agent will be skipped")

builder.set_execution_timeout(1800)  # 30 minute timeout for entire workflow
builder.set_node_timeout(600)  # 10 minute timeout per node

# Build the graph
# Note: When ENABLE_MULTI_STAGE=True, the aws_business_case agent result is not used
# Multi-stage generation happens after the graph completes
graph = builder.build()

# Get project context
project_context = get_project_context()
project_info = get_project_info_dict()

# Get uploaded filenames from project_info if available, otherwise use patterns
uploaded_files = project_info.get('uploadedFiles', {})
case_id = project_info.get('caseId', '')

# Use case-specific paths if case ID exists
if case_id:
    input_base = f"input/{case_id}/"
    logger.info(f"Using case-specific input directory: {input_base}")
else:
    input_base = "input/"
    logger.info("Using base input directory (no case ID)")

input_files1 = f"{input_base}{uploaded_files.get('itInventory', 'it-infrastructure-inventory.xlsx')}" if 'itInventory' in uploaded_files else f"{input_base}it-infrastructure-inventory.xlsx"
input_files2 = f"{input_base}{uploaded_files['rvTool'][0]}" if 'rvTool' in uploaded_files and uploaded_files['rvTool'] else f"{input_base}rvtool*.xlsx"
input_files3_excel = f"{input_base}{uploaded_files.get('atxExcel', 'atx_analysis.xlsx')}" if 'atxExcel' in uploaded_files else f"{input_base}atx_analysis.xlsx"
input_files3_pdf = f"{input_base}atx_report.pdf"
input_files3_pptx = f"{input_base}atx_business_case.pptx"
input_files4_mra = f"{input_base}aws-customer-migration-readiness-assessment.md"
input_files5_strategy = f"{input_base}aws-migration-strategy-6rs-framework.md"

logger.info(f"RVTools path: {input_files2}")

# Pre-read MRA file if it exists (Option 1: Direct Python call)
mra_content = None
mra_status = "Not Available"
try:
    from agents.analysis.mra_analysis import find_mra_file, read_pdf_file, read_docx_file, read_markdown_file
    
    mra_file = find_mra_file()
    if mra_file:
        logger.info(f"MRA file found: {mra_file}")
        
        # Read the file based on extension
        if mra_file.endswith('.pdf'):
            mra_content = read_pdf_file(mra_file)
        elif mra_file.endswith('.docx') or mra_file.endswith('.doc'):
            mra_content = read_docx_file(mra_file)
        elif mra_file.endswith('.md'):
            mra_content = read_markdown_file(mra_file)
        
        if mra_content and len(mra_content) > 1000:
            mra_status = "Available"
            logger.info(f"MRA content loaded: {len(mra_content)} characters")
        else:
            logger.warning(f"MRA file found but content is minimal: {len(mra_content) if mra_content else 0} characters")
    else:
        logger.info("No MRA file found in input directory")
except Exception as e:
    logger.error(f"Error reading MRA file: {e}")
    import traceback
    logger.error(traceback.format_exc())

import time
import uuid
import random
import pandas as pd
import glob
generation_id = int(time.time())
cache_buster = str(uuid.uuid4())[:8]  # Short unique ID
session_id = str(uuid.uuid4())  # Full UUID for session uniqueness
random_seed = random.randint(10000, 99999)  # Random number to break cache

# PRE-COMPUTE RVTOOLS SUMMARY IN PYTHON (bypasses LLM extraction and caching issues)
def get_rvtools_summary_precomputed(rvtools_path):
    """Pre-compute RVTools summary to avoid LLM extraction issues"""
    try:
        # Convert to absolute path
        abs_path = os.path.abspath(rvtools_path)
        logger.info(f"Pre-computing RVTools summary from: {abs_path}")
        
        # Check if file exists
        if not os.path.exists(abs_path):
            logger.error(f"File does not exist: {abs_path}")
            logger.info(f"Current working directory: {os.getcwd()}")
            logger.info(f"Directory contents: {os.listdir(os.path.dirname(abs_path)) if os.path.exists(os.path.dirname(abs_path)) else 'Directory not found'}")
            return None
        
        df = pd.read_excel(abs_path, sheet_name='vInfo')
        logger.info(f"✓ Loaded {len(df)} VMs from RVTools")
        
        # Filter to powered-on VMs only (powered-off VMs not included in migration)
        if 'Powerstate' in df.columns:
            df_powered_on = df[df['Powerstate'] == 'poweredOn']
            logger.info(f"✓ Filtered to {len(df_powered_on)} powered-on VMs")
        elif 'Power state' in df.columns:
            df_powered_on = df[df['Power state'] == 'poweredOn']
            logger.info(f"✓ Filtered to {len(df_powered_on)} powered-on VMs")
        else:
            df_powered_on = df
            logger.info("✓ No Powerstate column - using all VMs")
        
        # Calculate summary using the same logic as rv_tool_analysis
        from agents.analysis.rv_tool_analysis import generate_vm_summary
        summary = generate_vm_summary(df_powered_on)
        
        logger.info(f"✓✓✓ PRE-COMPUTED SUMMARY SUCCESS ✓✓✓")
        logger.info(f"    Total VMs: {summary['total_vms']}")
        logger.info(f"    Total vCPUs: {summary['total_vcpus']}")
        logger.info(f"    Total RAM (GB): {summary['total_memory_gb']:.1f}")
        logger.info(f"    Total Storage (TB): {summary['total_storage_tb']:.1f}")
        logger.info(f"    Windows VMs: {summary.get('windows_vms', 0)}")
        logger.info(f"    Linux VMs: {summary.get('linux_vms', 0)}")
        return summary
    except Exception as e:
        logger.error(f"✗ Error pre-computing RVTools summary: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

# PRE-COMPUTE IT INVENTORY SUMMARY IN PYTHON
def get_it_inventory_servers_for_eks(it_inventory_path):
    """
    Read actual server specs from IT Inventory for EKS analysis
    
    Returns list of server dictionaries with actual vCPU, RAM, OS, storage
    """
    try:
        abs_path = os.path.abspath(it_inventory_path)
        logger.info(f"Reading IT Inventory servers for EKS analysis: {abs_path}")
        
        if not os.path.exists(abs_path):
            logger.error(f"File does not exist: {abs_path}")
            return []
        
        # Read Servers sheet
        df_servers = pd.read_excel(abs_path, sheet_name='Servers')
        logger.info(f"✓ Loaded {len(df_servers)} servers from IT Inventory")
        
        servers = []
        for idx, row in df_servers.iterrows():
            # Extract server specs
            # Use HOSTNAME column (same as pricing) with fallback to Server Name
            server_name = row.get('HOSTNAME', row.get('Server Name', f'Server-{idx+1}'))
            vcpu = int(row.get('numCpus', 2))
            memory_gb = float(row.get('totalRAM (GB)', 4.0))
            os_name = str(row.get('osName', 'Linux'))
            
            # Handle storage - convert to numeric
            storage_gb = row.get('Storage-Total Disk Size (GB)', 100)
            if pd.isna(storage_gb) or storage_gb == 0 or storage_gb == '':
                storage_gb = 100
            else:
                # Parse storage - handle "500 GB" format
                if isinstance(storage_gb, str):
                    storage_gb = storage_gb.replace('GB', '').replace('gb', '').strip()
                try:
                    storage_gb = float(storage_gb)
                except:
                    storage_gb = 100
            
            servers.append({
                'vm_name': server_name,
                'name': server_name,  # Add 'name' key for EKS module
                'vcpu': vcpu,
                'memory': memory_gb,  # Add 'memory' key for EKS module
                'memory_gb': memory_gb,
                'os': os_name,
                'storage_gb': storage_gb
            })
        
        logger.info(f"✓ Prepared {len(servers)} servers for EKS analysis")
        logger.info(f"  - vCPU range: {min(s['vcpu'] for s in servers)} - {max(s['vcpu'] for s in servers)}")
        logger.info(f"  - RAM range: {min(s['memory_gb'] for s in servers):.1f} - {max(s['memory_gb'] for s in servers):.1f} GB")
        
        return servers
        
    except Exception as e:
        logger.error(f"✗ Error reading IT Inventory servers for EKS: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []


def get_rvtools_vms_for_eks(rvtools_path):
    """
    Read actual VM specs from RVTools for EKS analysis
    
    Returns list of VM dictionaries with actual vCPU, RAM, OS, storage, and VM names
    """
    try:
        abs_path = os.path.abspath(rvtools_path)
        logger.info(f"Reading RVTools VMs for EKS analysis: {abs_path}")
        
        if not os.path.exists(abs_path):
            logger.error(f"File does not exist: {abs_path}")
            return []
        
        # Read vInfo sheet (main VM information)
        df_vms = pd.read_excel(abs_path, sheet_name='vInfo')
        logger.info(f"✓ Loaded {len(df_vms)} VMs from RVTools")
        
        # Filter to powered-on VMs only
        if 'Powerstate' in df_vms.columns:
            df_vms = df_vms[df_vms['Powerstate'] == 'poweredOn'].copy()
            logger.info(f"✓ Filtered to {len(df_vms)} powered-on VMs")
        
        vms = []
        for idx, row in df_vms.iterrows():
            # Extract VM specs
            vm_name = row.get('VM', row.get('Name', f'VM-{idx+1}'))
            vcpu = int(row.get('CPUs', 2))
            memory_gb = float(row.get('Memory', 4.0)) / 1024.0  # RVTools stores in MB
            os_name = str(row.get('OS according to the VMware Tools', row.get('OS', 'Linux')))
            
            # Handle storage - sum all disks
            storage_gb = 0
            for i in range(1, 10):  # RVTools has Disk 1 through Disk 9
                disk_col = f'Disk {i}'
                if disk_col in row and pd.notna(row[disk_col]):
                    try:
                        disk_size = float(row[disk_col])
                        storage_gb += disk_size
                    except:
                        pass
            
            if storage_gb == 0:
                storage_gb = 100  # Default if no disk info
            
            vms.append({
                'vm_name': vm_name,
                'name': vm_name,  # Add 'name' key for EKS module
                'vcpu': vcpu,
                'memory': memory_gb,  # Add 'memory' key for EKS module
                'memory_gb': memory_gb,
                'os': os_name,
                'storage_gb': storage_gb
            })
        
        logger.info(f"✓ Prepared {len(vms)} VMs for EKS analysis")
        if vms:
            logger.info(f"  - vCPU range: {min(v['vcpu'] for v in vms)} - {max(v['vcpu'] for v in vms)}")
            logger.info(f"  - RAM range: {min(v['memory_gb'] for v in vms):.1f} - {max(v['memory_gb'] for v in vms):.1f} GB")
        
        return vms
        
    except Exception as e:
        logger.error(f"✗ Error reading RVTools VMs for EKS: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []


def get_it_inventory_summary_precomputed(it_inventory_path):
    """Pre-compute IT Inventory summary to avoid LLM extraction issues"""
    try:
        abs_path = os.path.abspath(it_inventory_path)
        logger.info(f"Pre-computing IT Inventory summary from: {abs_path}")
        
        if not os.path.exists(abs_path):
            logger.error(f"File does not exist: {abs_path}")
            return None
        
        # Read Servers sheet
        df_servers = pd.read_excel(abs_path, sheet_name='Servers')
        logger.info(f"✓ Loaded {len(df_servers)} servers from IT Inventory")
        
        # Read Databases sheet
        df_databases = pd.read_excel(abs_path, sheet_name='Databases')
        total_databases = len(df_databases)
        logger.info(f"✓ Loaded {total_databases} databases from IT Inventory")
        
        # Calculate server totals
        total_servers = len(df_servers)
        server_vcpus = int(df_servers['numCpus'].sum()) if 'numCpus' in df_servers.columns else 0
        server_memory_gb = float(df_servers['totalRAM (GB)'].sum()) if 'totalRAM (GB)' in df_servers.columns else 0.0
        
        # Calculate database totals
        db_vcpus = int(df_databases['CPU Cores'].sum()) if 'CPU Cores' in df_databases.columns else 0
        db_memory_gb = float(df_databases['RAM (GB)'].sum()) if 'RAM (GB)' in df_databases.columns else 0.0
        db_storage_gb = float(df_databases['Total Size (GB)'].sum()) if 'Total Size (GB)' in df_databases.columns else 0.0
        
        # Combined totals
        total_vcpus = server_vcpus + db_vcpus
        total_memory_gb = server_memory_gb + db_memory_gb
        
        # Handle server storage - convert to numeric first
        server_storage_gb = 0.0
        if 'Storage-Total Disk Size (GB)' in df_servers.columns:
            try:
                server_storage_gb = pd.to_numeric(df_servers['Storage-Total Disk Size (GB)'], errors='coerce').sum()
            except Exception as e:
                logger.warning(f"Could not calculate server storage: {e}")
        
        total_storage_tb = float((server_storage_gb + db_storage_gb) / 1024)
        
        # Count OS distribution (servers only)
        windows_vms = 0
        linux_vms = 0
        if 'osName' in df_servers.columns:
            for os_name in df_servers['osName']:
                if pd.notna(os_name):
                    os_lower = str(os_name).lower()
                    if 'windows' in os_lower:
                        windows_vms += 1
                    else:
                        linux_vms += 1
        
        summary = {
            'total_vms': total_servers,
            'total_databases': total_databases,
            'total_vcpus': int(total_vcpus),
            'total_memory_gb': float(total_memory_gb),
            'total_storage_tb': float(total_storage_tb),
            'windows_vms': windows_vms,
            'linux_vms': linux_vms
        }
        
        logger.info(f"✓✓✓ IT INVENTORY PRE-COMPUTED SUMMARY SUCCESS ✓✓✓")
        logger.info(f"    Total Servers: {summary['total_vms']}")
        logger.info(f"    Total Databases: {summary['total_databases']}")
        logger.info(f"    Total vCPUs (Servers + DBs): {summary['total_vcpus']}")
        logger.info(f"    Total RAM (GB) (Servers + DBs): {summary['total_memory_gb']:.1f}")
        logger.info(f"    Total Storage (TB) (Servers + DBs): {summary['total_storage_tb']:.1f}")
        logger.info(f"    Windows Servers: {summary['windows_vms']}")
        logger.info(f"    Linux Servers: {summary['linux_vms']}")
        return summary
    except Exception as e:
        logger.error(f"✗ Error pre-computing IT Inventory summary: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

# PRE-COMPUTE ATX SUMMARY IN PYTHON
def get_atx_summary_precomputed(atx_excel_path):
    """Pre-compute ATX summary to avoid LLM extraction issues"""
    try:
        abs_path = os.path.abspath(atx_excel_path)
        logger.info(f"Pre-computing ATX summary from: {abs_path}")
        
        if not os.path.exists(abs_path):
            logger.error(f"File does not exist: {abs_path}")
            return None
        
        # Use the existing ATX extractor
        from agents.pricing.atx_pricing_extractor import extract_atx_arr
        atx_data = extract_atx_arr(abs_path)
        
        if not atx_data.get('success'):
            logger.error(f"ATX extraction failed: {atx_data.get('error')}")
            return None
        
        # Convert to summary format
        summary = {
            'total_vms': atx_data['vm_count'],
            'total_vcpus': 0,  # ATX doesn't provide vCPU details
            'total_memory_gb': 0.0,  # ATX doesn't provide RAM details
            'total_storage_tb': 0.0,  # ATX doesn't provide storage details
            'windows_vms': atx_data.get('os_distribution', {}).get('Windows', 0),
            'linux_vms': atx_data.get('os_distribution', {}).get('Linux', 0),
            'total_arr': atx_data['total_arr'],
            'total_monthly': atx_data['total_monthly']
        }
        
        logger.info(f"✓✓✓ ATX PRE-COMPUTED SUMMARY SUCCESS ✓✓✓")
        logger.info(f"    Total VMs: {summary['total_vms']}")
        logger.info(f"    Windows VMs: {summary['windows_vms']}")
        logger.info(f"    Linux VMs: {summary['linux_vms']}")
        logger.info(f"    Total ARR: ${summary['total_arr']:,.2f}")
        logger.info(f"    Total Monthly: ${summary['total_monthly']:,.2f}")
        return summary
    except Exception as e:
        logger.error(f"✗ Error pre-computing ATX summary: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

# PRE-COMPUTE ATX PPT SUMMARY IN PYTHON
def get_atx_ppt_summary_precomputed(atx_ppt_path):
    """Pre-compute ATX PowerPoint summary to extract key slides"""
    try:
        abs_path = os.path.abspath(atx_ppt_path)
        logger.info(f"Pre-computing ATX PowerPoint summary from: {abs_path}")
        
        if not os.path.exists(abs_path):
            logger.error(f"File does not exist: {abs_path}")
            return None
        
        # Use the new ATX PowerPoint extractor
        from agents.analysis.atx_ppt_extractor import extract_atx_ppt_data
        atx_ppt_data = extract_atx_ppt_data(abs_path)
        
        if not atx_ppt_data.get('success'):
            logger.error(f"ATX PowerPoint extraction failed: {atx_ppt_data.get('error')}")
            return None
        
        # Extract data from slides
        scope = atx_ppt_data['assessment_scope']
        financial = atx_ppt_data['financial_overview']
        exec_summary = atx_ppt_data['executive_summary']
        
        # Convert to summary format
        summary = {
            'total_vms': scope.get('vm_count', 0),
            'total_vcpus': scope.get('vcpu_count', 0),
            'total_memory_gb': scope.get('ram_gb', 0),
            'total_storage_tb': scope.get('storage_tb', 0),
            'windows_vms': scope.get('windows_vms', 0),
            'linux_vms': scope.get('linux_vms', 0),
            'database_count': scope.get('database_count', 0),  # Extract database count
            'total_arr': financial.get('annual_cost', 0),
            'total_monthly': financial.get('monthly_cost', 0),
            'three_year_cost': financial.get('three_year_cost', 0),
            'executive_summary_content': exec_summary.get('content', ''),
            'financial_overview_content': financial.get('content', ''),
            'assessment_scope_content': scope.get('content', '')
        }
        
        logger.info(f"✓✓✓ ATX PPT PRE-COMPUTED SUMMARY SUCCESS ✓✓✓")
        logger.info(f"    Total VMs: {summary['total_vms']}")
        logger.info(f"    Windows VMs: {summary['windows_vms']}")
        logger.info(f"    Linux VMs: {summary['linux_vms']}")
        logger.info(f"    Database Count: {summary['database_count']}")
        logger.info(f"    Total ARR: ${summary['total_arr']:,.2f}")
        logger.info(f"    Total Monthly: ${summary['total_monthly']:,.2f}")
        return summary
    except Exception as e:
        logger.error(f"✗ Error pre-computing ATX PowerPoint summary: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

# Get pre-computed summaries based on which files exist (priority: RVTools > IT Inventory > ATX)
script_dir = os.path.dirname(os.path.abspath(__file__))  # agents/core/
agents_dir = os.path.dirname(script_dir)  # agents/
project_root = os.path.dirname(agents_dir)  # project root

# Try RVTools first (highest priority)
rvtools_summary = None
if has_rvtools:
    if 'rvTool' in uploaded_files and uploaded_files['rvTool']:
        rvtools_filename = uploaded_files['rvTool'][0]
    else:
        rvtools_filename = "RVTools_Export.xlsx"
    
    rvtools_path = os.path.join(input_folder_dir_path, case_id, rvtools_filename) if case_id else os.path.join(input_folder_dir_path, rvtools_filename)
    logger.info(f"="*80)
    logger.info(f"ATTEMPTING TO LOAD RVTOOLS SUMMARY")
    logger.info(f"="*80)
    logger.info(f"RVTools filename: {rvtools_filename}")
    logger.info(f"Input base: {input_base}")
    logger.info(f"Input folder path: {input_folder_dir_path}")
    logger.info(f"Case ID: {case_id}")
    logger.info(f"RVTools absolute path: {rvtools_path}")
    logger.info(f"File exists: {os.path.exists(rvtools_path)}")
    if not os.path.exists(rvtools_path):
        logger.error(f"RVTools file NOT FOUND at: {rvtools_path}")
        # List directory contents to debug
        input_dir = os.path.join(input_folder_dir_path, case_id) if case_id else input_folder_dir_path
        if os.path.exists(input_dir):
            logger.info(f"Contents of {input_dir}:")
            for item in os.listdir(input_dir):
                logger.info(f"  - {item}")
        else:
            logger.error(f"Input directory does not exist: {input_dir}")
    rvtools_summary = get_rvtools_summary_precomputed(rvtools_path)

# Try IT Inventory if RVTools not available
it_inventory_summary = None
if has_it_inventory and not rvtools_summary:
    if 'itInventory' in uploaded_files:
        it_inventory_filename = uploaded_files['itInventory']
    else:
        it_inventory_filename = "it-infrastructure-inventory.xlsx"
    
    it_inventory_path = os.path.join(input_folder_dir_path, case_id, it_inventory_filename) if case_id else os.path.join(input_folder_dir_path, it_inventory_filename)
    logger.info(f"="*80)
    logger.info(f"ATTEMPTING TO LOAD IT INVENTORY SUMMARY")
    logger.info(f"="*80)
    logger.info(f"IT Inventory filename: {it_inventory_filename}")
    logger.info(f"Input base: {input_base}")
    logger.info(f"Input folder path: {input_folder_dir_path}")
    logger.info(f"Case ID: {case_id}")
    logger.info(f"IT Inventory absolute path: {it_inventory_path}")
    logger.info(f"File exists: {os.path.exists(it_inventory_path)}")
    if not os.path.exists(it_inventory_path):
        logger.error(f"IT Inventory file NOT FOUND at: {it_inventory_path}")
        # List directory contents to debug
        input_dir = os.path.join(input_folder_dir_path, case_id) if case_id else input_folder_dir_path
        if os.path.exists(input_dir):
            logger.info(f"Contents of {input_dir}:")
            for item in os.listdir(input_dir):
                logger.info(f"  - {item}")
        else:
            logger.error(f"Input directory does not exist: {input_dir}")
    it_inventory_summary = get_it_inventory_summary_precomputed(it_inventory_path)

# Try ATX if neither RVTools nor IT Inventory available
atx_summary = None
if has_atx and not rvtools_summary and not it_inventory_summary:
    if 'atxPptx' in uploaded_files:
        atx_filename = uploaded_files['atxPptx']
    else:
        atx_filename = "atx_business_case.pptx"
    
    atx_path = os.path.join(input_folder_dir_path, case_id, atx_filename) if case_id else os.path.join(input_folder_dir_path, atx_filename)
    logger.info(f"="*80)
    logger.info(f"ATTEMPTING TO LOAD ATX SUMMARY")
    logger.info(f"="*80)
    logger.info(f"ATX filename: {atx_filename}")
    logger.info(f"Input base: {input_base}")
    logger.info(f"Input folder path: {input_folder_dir_path}")
    logger.info(f"Case ID: {case_id}")
    logger.info(f"ATX absolute path: {atx_path}")
    logger.info(f"File exists: {os.path.exists(atx_path)}")
    if not os.path.exists(atx_path):
        logger.error(f"ATX file NOT FOUND at: {atx_path}")
        # List directory contents to debug
        input_dir = os.path.join(input_folder_dir_path, case_id) if case_id else input_folder_dir_path
        if os.path.exists(input_dir):
            logger.info(f"Contents of {input_dir}:")
            for item in os.listdir(input_dir):
                logger.info(f"  - {item}")
        else:
            logger.error(f"Input directory does not exist: {input_dir}")
    atx_summary = get_atx_ppt_summary_precomputed(atx_path)

# Build data section based on what's available (priority: RVTools > IT Inventory > ATX)
if rvtools_summary:
    infrastructure_data_section = f"""
**═══════════════════════════════════════════════════════════════**
**PRE-COMPUTED RVTOOLS SUMMARY** (MANDATORY - Use these exact numbers)
**═══════════════════════════════════════════════════════════════**

These numbers were calculated directly from the RVTools file in Python.
DO NOT call rv_tool_analysis tool. DO NOT extract numbers from anywhere else.
USE ONLY THESE PRE-COMPUTED VALUES:

- **Total VMs for Migration**: {rvtools_summary['total_vms']}
- **Total vCPUs**: {rvtools_summary['total_vcpus']}
- **Total Memory (GB)**: {rvtools_summary['total_memory_gb']:.1f}
- **Total Storage (TB)**: {rvtools_summary['total_storage_tb']:.1f}
- **Windows VMs**: {rvtools_summary.get('windows_vms', 0)}
- **Linux VMs**: {rvtools_summary.get('linux_vms', 0)} (includes {rvtools_summary.get('other_vms', 0)} "Other" VMs treated as Linux)

**NOTE**: "Other" VMs are treated as Linux for pricing and reporting purposes.

**CRITICAL INSTRUCTIONS FOR ALL AGENTS**:
1. Use ONLY the numbers above in your analysis
2. Do NOT call rv_tool_analysis tool
3. Do NOT extract numbers from tool outputs
4. Do NOT use cached or remembered numbers
5. Copy these exact numbers into your response
6. **FOR ALL SECTIONS**: When reporting OS distribution, use EXACTLY these counts:
   - Windows VMs: {rvtools_summary.get('windows_vms', 0)}
   - Linux VMs: {rvtools_summary.get('linux_vms', 0)}
   - These counts MUST be consistent across ALL sections (Current State, Cost Analysis, etc.)

**═══════════════════════════════════════════════════════════════**
"""
    logger.info("✓ RVTools pre-computed summary added to task")
elif it_inventory_summary:
    infrastructure_data_section = f"""
**═══════════════════════════════════════════════════════════════**
**PRE-COMPUTED IT INVENTORY SUMMARY** (MANDATORY - Use these exact numbers)
**═══════════════════════════════════════════════════════════════**

These numbers were calculated directly from the IT Inventory file in Python.
DO NOT call it_analysis tool. DO NOT extract numbers from anywhere else.
USE ONLY THESE PRE-COMPUTED VALUES:

- **Total Servers for Migration**: {it_inventory_summary['total_vms']}
- **Total Databases (RDS)**: {it_inventory_summary['total_databases']}
- **Total vCPUs (Servers + Databases)**: {it_inventory_summary['total_vcpus']}
- **Total Memory GB (Servers + Databases)**: {it_inventory_summary['total_memory_gb']:.1f}
- **Total Storage TB (Servers + Databases)**: {it_inventory_summary['total_storage_tb']:.1f}
- **Windows Servers**: {it_inventory_summary['windows_vms']}
- **Linux Servers**: {it_inventory_summary['linux_vms']}

**CRITICAL INSTRUCTIONS FOR ALL AGENTS**:
1. Use ONLY the numbers above in your analysis
2. Do NOT call it_analysis tool
3. Do NOT extract numbers from tool outputs
4. Do NOT use cached or remembered numbers
5. Copy these exact numbers into your response
6. **FOR ALL SECTIONS**: When reporting infrastructure, ALWAYS include:
   - Total Servers: {it_inventory_summary['total_vms']}
   - Total Databases: {it_inventory_summary['total_databases']}
   - Total vCPUs, Memory, Storage (combined servers + databases)
7. **FOR ALL SECTIONS**: When reporting OS distribution, use EXACTLY these counts:
   - Windows Servers: {it_inventory_summary['windows_vms']}
   - Linux Servers: {it_inventory_summary['linux_vms']}
   - These counts MUST be consistent across ALL sections (Current State, Cost Analysis, etc.)

**═══════════════════════════════════════════════════════════════**
"""
    logger.info("✓ IT Inventory pre-computed summary added to task")
elif atx_summary:
    # Build ATX section with all extracted content
    assessment_scope_text = atx_summary.get('assessment_scope_content', 'Not available')
    exec_summary_text = atx_summary.get('executive_summary_content', 'Not available')
    financial_overview_text = atx_summary.get('financial_overview_content', 'Not available')
    
    infrastructure_data_section = f"""
**═══════════════════════════════════════════════════════════════**
**ATX PPT PRE-COMPUTED SUMMARY** (MANDATORY - Use these exact numbers and content)
**═══════════════════════════════════════════════════════════════**

These numbers and content were extracted directly from the ATX PowerPoint presentation.
DO NOT call read_pptx_file tool. DO NOT extract numbers from anywhere else.
USE ONLY THESE PRE-EXTRACTED VALUES AND CONTENT:

## ASSESSMENT SCOPE (from ATX PowerPoint)
- **Total VMs for Migration**: {atx_summary['total_vms']}
- **Windows VMs**: {atx_summary['windows_vms']}
- **Linux VMs**: {atx_summary['linux_vms']}
- **Total Databases**: {atx_summary.get('database_count', 0)}
- **Total Storage**: {atx_summary['total_storage_tb']:.2f} TB
- **Total vCPUs**: {atx_summary.get('total_vcpus', 'Not specified in ATX')}
- **Total RAM**: {atx_summary.get('total_memory_gb', 'Not specified in ATX')} GB

**🚨 CRITICAL - DATABASE COUNT**: {atx_summary.get('database_count', 0)} databases
- IF database count is 0: This is VM-ONLY migration, NO RDS, NO database costs
- IF database count > 0: Include both EC2 and RDS costs

**Full Assessment Scope Content**:
{assessment_scope_text}

## EXECUTIVE SUMMARY (from ATX PowerPoint)
**Use this content for Executive Summary section**:
{exec_summary_text}

## FINANCIAL OVERVIEW (from ATX PowerPoint)
- **Monthly AWS Cost**: ${atx_summary['total_monthly']:,.2f}
- **Annual AWS Cost (ARR)**: ${atx_summary['total_arr']:,.2f}
- **3-Year Total Cost**: ${atx_summary.get('three_year_cost', atx_summary['total_arr'] * 3):,.2f}

**Full Financial Overview Content (USE AS-IS in Cost Analysis section)**:
{financial_overview_text}

**CRITICAL INSTRUCTIONS FOR ALL AGENTS**:
1. Use ONLY the numbers and content above in your analysis
2. For Executive Summary: Extract key points from the ATX Executive Summary content
3. For Cost Analysis: Include the Financial Overview content AS-IS
4. For Current State: Use Assessment Scope numbers ONLY
5. DO NOT add information not present in the ATX content
6. DO NOT hallucinate workload types, applications, or technical details
7. If information is missing, state "Not provided in ATX assessment"
8. **FOR ALL SECTIONS**: When reporting OS distribution, use EXACTLY these counts:
   - Windows VMs: {atx_summary['windows_vms']}
   - Linux VMs: {atx_summary['linux_vms']}
   - These counts MUST be consistent across ALL sections

**═══════════════════════════════════════════════════════════════**
"""
    logger.info("✓ ATX PPT pre-computed summary added to task")
else:
    infrastructure_data_section = """
**Infrastructure Summary**: Not available - file could not be read
"""
    logger.warning("⚠ Infrastructure summary could not be pre-computed")

# Build agent task with MRA content if available
# Truncate MRA to 10000 chars to prevent token overflow
if mra_content:
    mra_summary = f"--- MRA SUMMARY (first 10000 chars) ---\n{mra_content[:10000]}\n--- END MRA SUMMARY ---"
    mra_section = f"""
    **MRA STATUS**: {mra_status}
    
    **MRA CONTENT PROVIDED** - MRA file available for analysis
    
    {mra_summary}
    """
else:
    mra_section = "**MRA STATUS**: Not Available"

# Extract timeline from project description
import re
def extract_timeline_months(description):
    """Extract migration timeline in months from project description"""
    if not description:
        return None
    # Look for patterns like "18 months", "24 months", "within 18 months", "next 18 months"
    patterns = [
        r'within\s+(?:the\s+)?(?:next\s+)?(\d+)\s+months',
        r'(?:next|in)\s+(\d+)\s+months',
        r'(\d+)[-\s]month',
    ]
    for pattern in patterns:
        match = re.search(pattern, description, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None

timeline_months = extract_timeline_months(project_info.get('projectDescription', ''))
timeline_note = f"\n**⚠️ MIGRATION TIMELINE REQUIREMENT: {timeline_months} MONTHS ⚠️**\n**ALL migration phases, waves, and timelines MUST fit within {timeline_months} months total.**\n**DO NOT exceed {timeline_months} months under any circumstances.**\n" if timeline_months else ""

# Extract project details for ATX deterministic generation
customer_name = project_info.get('customerName', 'Customer')
project_name = project_info.get('projectName', 'AWS Migration')
target_region = project_info.get('awsRegion', 'us-east-1')

logger.info(f"Extracted timeline: {timeline_months} months" if timeline_months else "No timeline found in project description")

# Cache busting: Add unique session identifier
cache_breaking_prefix = f"[SESSION:{session_id[:8]}|GEN:{generation_id}|SEED:{random_seed}] "

agent_task = f"""{cache_breaking_prefix}Create a comprehensive business case to migrate on-premises IT workload to AWS.

{project_context}
{timeline_note}

    {infrastructure_data_section}
    
    **Input Data Sources:**
        1. IT Infrastructure Inventory: {input_files1}
        2. RVTool Assessment Data: {input_files2} (summary pre-computed above)
        3. AWS Transform for VMware (ATX) Assessment:
           - VMware Environment Data: {input_files3_excel}
           - Technical Assessment Report: {input_files3_pdf}
           - Business Case Presentation: {input_files3_pptx}
        4. Migration Readiness Assessment (MRA): {input_files4_mra}
        5. Migration Strategy Framework (6Rs): {input_files5_strategy}
        
    {mra_section}
        
    **CRITICAL**: Use the PRE-COMPUTED RVTOOLS SUMMARY numbers provided above.
   """

logger.info("="*80)
logger.info("STARTING AGENT WORKFLOW")
logger.info("="*80)
logger.info(f"Project: {project_info.get('projectName', 'N/A')}")
logger.info(f"Customer: {project_info.get('customerName', 'N/A')}")
logger.info(f"Region: {project_info.get('awsRegion', 'N/A')}")
logger.info(f"Description: {project_info.get('projectDescription', 'N/A')}")
logger.info("="*80)

logger.info("Executing agent graph...")
import time as _time
from strands.types.exceptions import MaxTokensReachedException

_max_graph_retries = 3
_graph_result = None
_graph_error = None

for _attempt in range(1, _max_graph_retries + 1):
    try:
        _graph_result = graph(agent_task)
        logger.info("Agent graph execution completed")
        break
    except MaxTokensReachedException as _max_tok_exc:
        # MaxTokensReachedException means an agent hit its output limit but the graph
        # may have partially completed. This is not retryable - treat as best-effort completion.
        logger.warning(f"Graph agent hit max_tokens limit: {_max_tok_exc}")
        logger.warning("Proceeding with partial results (some agent outputs may be truncated)")
        # Create a minimal result object so downstream code can proceed
        _graph_result = graph.get_state() if hasattr(graph, 'get_state') else None
        if _graph_result is None:
            # Build a stub result that allows multi-stage generation and logging to proceed
            class _StubNode:
                def __init__(self, node_id):
                    self.node_id = node_id
                    self.execution_time = 0
                    self.status = 'max_tokens_reached'
            class _StubResult:
                results = {}
                status = 'partial'
                execution_order = []
                completed_nodes = 0
                total_nodes = 0
                execution_time = 0
                accumulated_usage = 'N/A (partial execution)'
            _graph_result = _StubResult()
        break
    except Exception as _graph_exc:
        _graph_error = _graph_exc
        error_str = str(_graph_exc).lower()
        # Retry on transient streaming/throttling errors
        is_retryable = any(keyword in error_str for keyword in [
            'stream', 'throttl', 'timeout', 'serviceunavailable',
            'internalserver', 'modelstreamerror', 'connection'
        ])
        if is_retryable and _attempt < _max_graph_retries:
            wait_time = 2 ** _attempt * 5  # Exponential backoff: 10s, 20s
            logger.warning(f"Graph execution failed (attempt {_attempt}/{_max_graph_retries}): {_graph_exc}")
            logger.info(f"Retrying in {wait_time}s...")
            _time.sleep(wait_time)
        else:
            logger.error(f"Graph execution failed (attempt {_attempt}/{_max_graph_retries}): {_graph_exc}")
            import traceback
            logger.error(traceback.format_exc())
            raise Exception(f"Agent graph execution failed after {_attempt} attempts: {_graph_exc}") from _graph_exc

result = _graph_result


# ============================================================================
# DETERMINISTIC EKS RECOMMENDATION FUNCTION
# ============================================================================

def calculate_eks_recommendation(option1_3yr, option2_3yr, option3_3yr, 
                                 option1_monthly, option2_monthly, option3_monthly,
                                 ec2_vm_count, eks_vm_count):
    """
    Deterministic EKS recommendation based on 4-tier decision tree.
    
    This function makes the recommendation decision using pure Python logic,
    ensuring 100% deterministic and testable results. The LLM only generates
    the natural language explanation based on this pre-calculated decision.
    
    Args:
        option1_3yr: 3-year cost for Option 1 (EC2 Instance SP)
        option2_3yr: 3-year cost for Option 2 (Compute SP)
        option3_3yr: 3-year cost for Option 3 (Hybrid EC2+EKS)
        option1_monthly: Monthly cost for Option 1
        option2_monthly: Monthly cost for Option 2
        option3_monthly: Monthly cost for Option 3
        ec2_vm_count: Number of VMs staying on EC2
        eks_vm_count: Number of VMs migrating to EKS
    
    Returns:
        dict with:
        - recommended_option: 'option1', 'option2', or 'option3'
        - recommended_option_name: Full name of recommended option
        - tier: 'tier1', 'tier2', 'tier3', or 'tier4'
        - tier_name: Human-readable tier name
        - cost_difference_dollars: Dollar difference vs cheapest EC2
        - cost_difference_pct: Percentage difference vs cheapest EC2
        - cheapest_ec2_option: 'option1' or 'option2'
        - rationale_template: Key for LLM to expand into full rationale
        - comparison_details: Dict with all cost comparisons
    """
    from agents.config.config import EKS_RECOMMENDATION_THRESHOLDS
    
    # Get thresholds from config
    tier2_max_premium = EKS_RECOMMENDATION_THRESHOLDS.get('tier2_max_premium', 0.20)
    tier3_max_premium = EKS_RECOMMENDATION_THRESHOLDS.get('tier3_max_premium', 0.50)
    
    # Determine cheapest EC2 option
    if option1_3yr <= option2_3yr:
        cheapest_ec2_option = 'option1'
        cheapest_ec2_cost = option1_3yr
        cheapest_ec2_monthly = option1_monthly
        cheapest_ec2_name = 'Option 1 (3-Year EC2 Instance Savings Plan)'
    else:
        cheapest_ec2_option = 'option2'
        cheapest_ec2_cost = option2_3yr
        cheapest_ec2_monthly = option2_monthly
        cheapest_ec2_name = 'Option 2 (3-Year Compute Savings Plan)'
    
    # Calculate cost difference
    cost_diff_dollars = option3_3yr - cheapest_ec2_cost
    cost_diff_pct = (cost_diff_dollars / cheapest_ec2_cost * 100) if cheapest_ec2_cost > 0 else 0
    
    # Apply 4-tier decision tree
    if cost_diff_pct < 0:
        # Tier 1: Option 3 is CHEAPER
        tier = 'tier1'
        tier_name = 'Tier 1: EKS is Cheaper'
        recommended_option = 'option3'
        recommended_option_name = 'Option 3 (Hybrid EC2 + EKS)'
        rationale_template = 'eks_cheaper'
        rationale_text = f"Option 3 recommended: Lowest total cost (${option3_monthly:,.2f}/month) plus strategic benefits of containerization, scalability, and modernization. Saves ${abs(cost_diff_dollars):,.2f} over 3 years compared to {cheapest_ec2_name}."
        
    elif cost_diff_pct <= (tier2_max_premium * 100):
        # Tier 2: Option 3 costs 0-20% MORE (default)
        tier = 'tier2'
        tier_name = f'Tier 2: EKS Premium within {tier2_max_premium*100:.0f}% Threshold'
        recommended_option = 'option3'
        recommended_option_name = 'Option 3 (Hybrid EC2 + EKS)'
        rationale_template = 'strategic_value'
        rationale_text = f"Option 3 recommended: Strategic investment in modernization worth {cost_diff_pct:.2f}% premium (${cost_diff_dollars:,.2f} over 3 years) for container-native architecture, auto-scaling capabilities, and DevOps velocity. The {eks_vm_count} Linux VMs migrating to EKS will benefit from Kubernetes orchestration, while {ec2_vm_count} VMs remain on EC2 for optimal cost-performance balance."
        
    elif cost_diff_pct <= (tier3_max_premium * 100):
        # Tier 3: Option 3 costs 20-50% MORE (default)
        tier = 'tier3'
        tier_name = f'Tier 3: EKS Premium {tier2_max_premium*100:.0f}%-{tier3_max_premium*100:.0f}%'
        recommended_option = cheapest_ec2_option
        recommended_option_name = cheapest_ec2_name
        rationale_template = 'cost_sensitive'
        rationale_text = f"{cheapest_ec2_name} recommended for cost optimization (${cheapest_ec2_monthly:,.2f}/month). Option 3 (Hybrid EC2 + EKS) available as strategic alternative for {cost_diff_pct:.2f}% premium (${cost_diff_dollars:,.2f} over 3 years) if containerization and Kubernetes ecosystem are organizational priorities."
        
    else:
        # Tier 4: Option 3 costs MORE than 50% (default)
        tier = 'tier4'
        tier_name = f'Tier 4: EKS Premium exceeds {tier3_max_premium*100:.0f}%'
        recommended_option = cheapest_ec2_option
        recommended_option_name = cheapest_ec2_name
        rationale_template = 'not_viable'
        rationale_text = f"{cheapest_ec2_name} recommended: Most cost-effective for current scale (${cheapest_ec2_monthly:,.2f}/month). Option 3 costs {cost_diff_pct:.2f}% more (${cost_diff_dollars:,.2f} over 3 years). EKS becomes more competitive at larger scale (50+ VMs) due to consolidation benefits and fixed cost amortization."
    
    # Build comparison details
    comparison_details = {
        'option1_3yr': option1_3yr,
        'option1_monthly': option1_monthly,
        'option2_3yr': option2_3yr,
        'option2_monthly': option2_monthly,
        'option3_3yr': option3_3yr,
        'option3_monthly': option3_monthly,
        'cheapest_ec2_option': cheapest_ec2_option,
        'cheapest_ec2_cost': cheapest_ec2_cost,
        'cheapest_ec2_monthly': cheapest_ec2_monthly,
        'option1_vs_option3_diff': option3_3yr - option1_3yr,
        'option1_vs_option3_pct': ((option3_3yr - option1_3yr) / option1_3yr * 100) if option1_3yr > 0 else 0,
        'option2_vs_option3_diff': option3_3yr - option2_3yr,
        'option2_vs_option3_pct': ((option3_3yr - option2_3yr) / option2_3yr * 100) if option2_3yr > 0 else 0,
    }
    
    return {
        'recommended_option': recommended_option,
        'recommended_option_name': recommended_option_name,
        'tier': tier,
        'tier_name': tier_name,
        'cost_difference_dollars': cost_diff_dollars,
        'cost_difference_pct': cost_diff_pct,
        'cheapest_ec2_option': cheapest_ec2_option,
        'cheapest_ec2_name': cheapest_ec2_name,
        'rationale_template': rationale_template,
        'rationale_text': rationale_text,
        'comparison_details': comparison_details,
        'thresholds': {
            'tier2_max_premium_pct': tier2_max_premium * 100,
            'tier3_max_premium_pct': tier3_max_premium * 100
        },
        'vm_distribution': {
            'ec2_vm_count': ec2_vm_count,
            'eks_vm_count': eks_vm_count,
            'total_vm_count': ec2_vm_count + eks_vm_count
        }
    }


# ============================================================================
# ============================================================================
# EKS ANALYSIS (Phase 7 - Final Integration)
# ============================================================================
logger.info("="*80)
logger.info("EKS CONTAINER MIGRATION ANALYSIS")
logger.info("="*80)

eks_results = None
eks_enabled = EKS_CONFIG.get('enabled', True)
eks_strategy = EKS_CONFIG.get('strategy', 'hybrid')

logger.info("="*80)
logger.info("EKS ANALYSIS CHECK")
logger.info("="*80)
logger.info(f"EKS Analysis: {'ENABLED' if eks_enabled else 'DISABLED'}")
logger.info(f"EKS Strategy: {eks_strategy}")
logger.info(f"RVTools Summary Available: {rvtools_summary is not None}")
logger.info(f"IT Inventory Summary Available: {it_inventory_summary is not None}")
logger.info(f"ATX Summary Available: {atx_summary is not None}")

if eks_enabled and eks_strategy != 'disabled':
    logger.info("✓ EKS analysis will be attempted")
    try:
        # Get VMs from pre-computed summary
        vms_for_eks = []
        
        # Build VM list from pre-computed summary
        if rvtools_summary:
            # Use RVTools data - READ ACTUAL VM SPECS
            logger.info("Using RVTools data for EKS analysis")
            
            # Get actual VM specs from RVTools
            rvtools_path = os.path.join(project_root, input_base, rvtools_filename)
            logger.info(f"RVTools path: {rvtools_path}")
            logger.info(f"RVTools file exists: {os.path.exists(rvtools_path)}")
            actual_vms = get_rvtools_vms_for_eks(rvtools_path)
            
            if actual_vms:
                logger.info(f"✓ Using {len(actual_vms)} actual VMs from RVTools")
                vms_for_eks = actual_vms
            else:
                # Fallback to synthetic data if reading fails
                logger.warning("⚠ Could not read actual VMs, using synthetic data")
                total_vms = rvtools_summary['total_vms']
                windows_vms = rvtools_summary.get('windows_vms', 0)
                linux_vms = rvtools_summary.get('linux_vms', 0)
                
                # Estimate VM distribution by size (industry standard)
                small_pct = 0.35
                medium_pct = 0.28
                large_pct = 0.25
                xlarge_pct = 0.12
                
                # Create representative VMs for categorization
                for i in range(total_vms):
                    is_windows = i < windows_vms
                    os_type = 'Windows' if is_windows else 'Linux'
                    
                    # Determine size based on distribution
                    if i < int(total_vms * small_pct):
                        vcpu = 2
                        memory_gb = 4
                    elif i < int(total_vms * (small_pct + medium_pct)):
                        vcpu = 4
                        memory_gb = 8
                    elif i < int(total_vms * (small_pct + medium_pct + large_pct)):
                        vcpu = 8
                        memory_gb = 16
                    else:
                        vcpu = 16
                        memory_gb = 32
                    
                    vms_for_eks.append({
                        'vm_name': f'VM-{i+1}',
                        'name': f'VM-{i+1}',
                        'vcpu': vcpu,
                        'memory_gb': memory_gb,
                        'os': os_type,
                        'storage_gb': 100
                    })
        
        elif it_inventory_summary:
            # Use IT Inventory data - READ ACTUAL SERVER SPECS
            logger.info("Using IT Inventory data for EKS analysis")
            
            # Get actual server specs from IT Inventory
            it_inventory_path = os.path.join(project_root, input_base, it_inventory_filename)
            actual_servers = get_it_inventory_servers_for_eks(it_inventory_path)
            
            if actual_servers:
                logger.info(f"✓ Using {len(actual_servers)} actual servers from IT Inventory")
                vms_for_eks = actual_servers
            else:
                # Fallback to synthetic data if reading fails
                logger.warning("Could not read actual servers, using synthetic data")
                total_servers = it_inventory_summary['total_vms']
                windows_servers = it_inventory_summary['windows_vms']
                linux_servers = it_inventory_summary['linux_vms']
                
                # Create representative servers
                for i in range(total_servers):
                    is_windows = i < windows_servers
                    os_type = 'Windows' if is_windows else 'Linux'
                    
                    # Estimate size distribution
                    if i < int(total_servers * 0.35):
                        vcpu = 2
                        memory_gb = 4
                    elif i < int(total_servers * 0.63):
                        vcpu = 4
                        memory_gb = 8
                    elif i < int(total_servers * 0.88):
                        vcpu = 8
                        memory_gb = 16
                    else:
                        vcpu = 16
                        memory_gb = 32
                    
                    vms_for_eks.append({
                        'vm_name': f'Server-{i+1}',
                        'name': f'Server-{i+1}',
                        'vcpu': vcpu,
                        'memory': memory_gb,
                        'memory_gb': memory_gb,
                        'os': os_type,
                        'storage_gb': 100
                    })
        
        if vms_for_eks:
            logger.info(f"Analyzing {len(vms_for_eks)} VMs for EKS migration")
            
            # Step 1: Categorize VMs
            logger.info("Step 1: Categorizing VMs by OS and size...")
            vm_categorization = categorize_vms_for_eks(vms_for_eks, strategy=eks_strategy, logger=logger)
            logger.info(f"  - Linux VMs for EKS: {vm_categorization['summary']['eks_linux']}")
            logger.info(f"  - Windows VMs for EKS: {vm_categorization['summary']['eks_windows']}")
            logger.info(f"  - VMs for EC2: {vm_categorization['summary']['ec2_total']}")
            logger.info(f"  - Total EKS candidates: {vm_categorization['summary']['eks_total']}")
            
            if vm_categorization['summary']['eks_total'] > 0:
                # Step 2: Calculate cluster size
                logger.info("Step 2: Calculating EKS cluster size...")
                eks_vms = vm_categorization['eks_linux'] + vm_categorization['eks_windows']
                cluster_config = calculate_eks_cluster_size(eks_vms, region=target_region)
                
                # Calculate total nodes and consolidation ratio
                total_nodes = cluster_config.get('total_nodes', 0)  # Use pre-calculated total
                total_vms = cluster_config.get('total_vms', 0)
                required_vcpu = cluster_config.get('required_vcpu', 0)
                
                # Calculate actual vCPU capacity from worker nodes
                actual_vcpu = sum(node.get('vcpu', 0) * node.get('count', 1) for node in cluster_config.get('all_worker_nodes', []))
                consolidation_ratio = (sum(vm.get('vcpu', 0) for vm in eks_vms) / required_vcpu) if required_vcpu > 0 else 1.0
                
                logger.info(f"  - Worker nodes: {total_nodes}")
                logger.info(f"  - Total vCPU capacity: {actual_vcpu}")
                logger.info(f"  - Required vCPU (after consolidation): {required_vcpu:.1f}")
                logger.info(f"  - Consolidation ratio: {consolidation_ratio:.2f}x")
                
                # Step 3: Calculate EKS costs (in parallel with EC2 if needed)
                logger.info("Step 3: Calculating EKS costs...")
                eks_costs = calculate_eks_costs_async(
                    eks_cluster=cluster_config,
                    num_clusters=2,
                    region=target_region
                )
                monthly_cost = eks_costs.get('monthly_avg', 0)
                annual_cost = monthly_cost * 12
                logger.info(f"  - Monthly cost: ${monthly_cost:,.2f}")
                logger.info(f"  - Annual cost: ${annual_cost:,.2f}")
                logger.info(f"  - 3-Year total: ${eks_costs.get('total_3yr', 0):,.2f}")
                
                # Step 3.5: Calculate EKS backup costs
                logger.info("Step 3.5: Calculating EKS backup costs...")
                from agents.pricing.backup_pricing import calculate_eks_backup_costs
                eks_backup_costs = calculate_eks_backup_costs(eks_vms, target_region)
                logger.info(f"  - EKS backup monthly: ${eks_backup_costs['monthly_cost']:,.2f}")
                logger.info(f"  - Stateful workloads: {eks_backup_costs['stateful_workloads']}/{eks_backup_costs['total_workloads']}")
                logger.info(f"  - Backup storage: {eks_backup_costs['backup_storage_gb']:.0f} GB")
                
                # Add backup costs to EKS total
                eks_costs['backup_monthly'] = eks_backup_costs['monthly_cost']
                eks_costs['backup_annual'] = eks_backup_costs['annual_cost']
                eks_costs['backup_details'] = eks_backup_costs
                eks_costs['monthly_avg'] = eks_costs.get('monthly_avg', 0) + eks_backup_costs['monthly_cost']
                eks_costs['total_3yr'] = eks_costs.get('monthly_avg', 0) * 36
                
                # Step 4: Parse MRA for EKS (if available)
                mra_scores = None
                if has_mra:
                    try:
                        logger.info("Step 4: Parsing MRA for EKS readiness...")
                        mra_scores = parse_mra_for_eks()
                        if mra_scores:
                            logger.info(f"  - Cloud readiness: {mra_scores.get('cloud_readiness', 'N/A')}/5")
                            logger.info(f"  - Container expertise: {mra_scores.get('container_expertise', 'N/A')}/5")
                            logger.info(f"  - DevOps maturity: {mra_scores.get('devops_maturity', 'N/A')}/5")
                    except Exception as e:
                        logger.warning(f"Could not parse MRA for EKS: {e}")
                
                # Step 5: Get strategy recommendation
                logger.info("Step 5: Generating EKS strategy recommendation...")
                strategy_recommendation = recommend_eks_strategy(
                    mra_data=mra_scores,
                    project_info=project_info,
                    vm_inventory=vms_for_eks
                )
                logger.info(f"  - Recommended strategy: {strategy_recommendation['recommended_strategy'].upper()}")
                logger.info(f"  - Confidence: {strategy_recommendation['confidence'].upper()}")
                
                # Step 6: Allocate costs to VMs
                logger.info("Step 6: Allocating EKS costs to VMs...")
                vms_with_eks_costs = allocate_eks_costs_to_vms(
                    eks_vms=eks_vms,
                    eks_costs=eks_costs
                )
                
                # Store results
                eks_results = {
                    'vm_categorization': vm_categorization,
                    'cluster_config': cluster_config,
                    'eks_costs': eks_costs,
                    'strategy_recommendation': strategy_recommendation,
                    'mra_scores': mra_scores,
                    'vms_with_costs': vms_with_eks_costs
                }
                
                # Calculate hybrid costs (EC2 for non-EKS VMs + EKS costs)
                logger.info("Calculating hybrid EC2+EKS costs...")
                ec2_only_vms = vm_categorization['ec2']
                ec2_only_vm_count = len(ec2_only_vms)
                eks_vm_count = len(eks_vms)
                total_vm_count = ec2_only_vm_count + eks_vm_count
                
                # Calculate EC2 costs for non-EKS VMs only
                # CRITICAL: Read actual costs from EC2 Details sheet (includes right-sizing)
                # DO NOT use proportional calculation - that misses right-sizing optimizations
                total_vcpu_all = sum(vm.get('vcpu', 0) for vm in vms_for_eks)
                ec2_vcpu = sum(vm.get('vcpu', 0) for vm in ec2_only_vms)
                eks_vcpu = sum(vm.get('vcpu', 0) for vm in eks_vms)
                
                logger.info(f"  - Total VMs: {total_vm_count} ({ec2_only_vm_count} EC2 + {eks_vm_count} EKS)")
                logger.info(f"  - Total vCPU: {total_vcpu_all} ({ec2_vcpu} EC2 + {eks_vcpu} EKS)")
                
                # Get EC2 costs from Excel file
                try:
                    import pandas as pd
                    import glob
                    from agents.utils.project_context import get_case_id
                    
                    # Try to find Excel file in case-specific output folder first
                    case_id = get_case_id()
                    excel_files = []
                    
                    logger.info(f"[EKS Hybrid] Looking for Excel file - case_id: {case_id}, output_dir: {output_folder_dir_path}")
                    
                    if case_id:
                        case_output_dir = os.path.join(output_folder_dir_path, case_id)
                        logger.info(f"[EKS Hybrid] Checking case-specific directory: {case_output_dir}")
                        if os.path.exists(case_output_dir):
                            # Try IT Inventory in case folder
                            excel_files = glob.glob(os.path.join(case_output_dir, 'it_inventory_aws_pricing_*.xlsx'))
                            
                            # If no IT Inventory, try RVTools in case folder
                            if not excel_files:
                                rvtools_path = os.path.join(case_output_dir, 'vm_to_ec2_mapping.xlsx')
                                logger.info(f"[EKS Hybrid] Checking RVTools path: {rvtools_path}")
                                if os.path.exists(rvtools_path):
                                    excel_files = [rvtools_path]
                                    logger.info(f"[EKS Hybrid] ✓ Found RVTools file in case directory")
                                else:
                                    logger.warning(f"[EKS Hybrid] RVTools file not found at: {rvtools_path}")
                        else:
                            logger.warning(f"[EKS Hybrid] Case output directory does not exist: {case_output_dir}")
                    
                    # Fallback to root output folder if not found in case folder
                    if not excel_files:
                        # Try IT Inventory first
                        excel_files = glob.glob(os.path.join(output_folder_dir_path, 'it_inventory_aws_pricing_*.xlsx'))
                        
                        # If no IT Inventory, try RVTools
                        if not excel_files:
                            rvtools_path = os.path.join(output_folder_dir_path, 'vm_to_ec2_mapping.xlsx')
                            if os.path.exists(rvtools_path):
                                excel_files = [rvtools_path]
                    
                    if excel_files:
                        latest_excel = max(excel_files, key=os.path.getmtime)
                        
                        # Determine sheet name based on file type
                        # IT Inventory uses: EC2_Option1_Instance_SP
                        # RVTools uses: EC2 Details - Option 1
                        sheet_name = 'EC2_Option1_Instance_SP' if 'it_inventory' in os.path.basename(latest_excel) else 'EC2 Details - Option 1'
                        
                        # Read EC2 Details sheet to get actual costs
                        df_ec2_details = pd.read_excel(latest_excel, sheet_name=sheet_name)
                        
                        # Create EC2 cost map for ALL VMs (for later use in EKS Excel)
                        ec2_cost_map_all_vms = {}
                        
                        # IT Inventory uses 'Hostname', RVTools uses 'VM Name'
                        vm_name_col = 'Hostname' if 'it_inventory' in os.path.basename(latest_excel) else df_ec2_details.columns[0]
                        # IT Inventory uses 'Monthly Cost', RVTools uses 'Total Monthly Cost ($)'
                        cost_col = 'Monthly Cost' if 'it_inventory' in os.path.basename(latest_excel) else 'Total Monthly Cost ($)'
                        
                        for idx, row in df_ec2_details.iterrows():
                            vm_name = row.get(vm_name_col, '')
                            vm_cost = row.get(cost_col, 0)
                            if vm_name and vm_cost:
                                # Remove $ and commas if it's a string
                                if isinstance(vm_cost, str):
                                    vm_cost = vm_cost.replace('$', '').replace(',', '')
                                ec2_cost_map_all_vms[vm_name] = float(vm_cost)
                        
                        logger.info(f"  - Loaded EC2 costs for {len(ec2_cost_map_all_vms)} VMs from pricing sheet")
                        
                        # Debug: Show sample VM names from cost map
                        if ec2_cost_map_all_vms:
                            sample_names = list(ec2_cost_map_all_vms.keys())[:5]
                            logger.info(f"  - Sample VM names in cost map: {sample_names}")
                        
                        # Sum up costs for VMs staying on EC2
                        ec2_only_monthly = 0
                        matched_vms = 0
                        missing_vms = []
                        
                        for vm in ec2_only_vms:
                            vm_name = vm.get('name', vm.get('vm_name', ''))
                            if vm_name in ec2_cost_map_all_vms:
                                ec2_only_monthly += ec2_cost_map_all_vms[vm_name]
                                matched_vms += 1
                            else:
                                missing_vms.append(vm_name)
                        
                        logger.info(f"  - Matched {matched_vms}/{ec2_only_vm_count} EC2 VMs in pricing sheet")
                        
                        if missing_vms:
                            logger.warning(f"  - Missing VMs in pricing sheet: {missing_vms[:10]}")  # Show first 10
                            
                            # FALLBACK: If most VMs are missing, use proportional calculation
                            if matched_vms < ec2_only_vm_count * 0.5:  # Less than 50% matched
                                logger.warning(f"  - Less than 50% VMs matched, using proportional fallback")
                                # Calculate total cost from all VMs in cost map
                                total_cost_all_vms = sum(ec2_cost_map_all_vms.values())
                                # Proportionally allocate to EC2-only VMs
                                ec2_only_monthly = total_cost_all_vms * (ec2_only_vm_count / len(ec2_cost_map_all_vms))
                                logger.info(f"  - Fallback EC2 cost: ${ec2_only_monthly:,.2f}/month (proportional)")
                        
                        logger.info(f"  - EC2 cost for {ec2_only_vm_count} VMs (from pricing sheet): ${ec2_only_monthly:,.2f}/month")
                    else:
                        # Fallback: estimate based on VM count
                        logger.warning("Could not find Excel file, using fallback calculation")
                        ec2_only_monthly = 2676.60 * (ec2_only_vm_count / total_vm_count)
                        
                except Exception as e:
                    logger.error(f"Error reading EC2 costs from Excel: {e}")
                    import traceback
                    traceback.print_exc()
                    # Fallback calculation
                    ec2_only_monthly = 2676.60 * (ec2_only_vm_count / total_vm_count)
                
                # Calculate hybrid costs
                hybrid_costs = {
                    'ec2_monthly': ec2_only_monthly,
                    'ec2_annual': ec2_only_monthly * 12,
                    'ec2_3yr': ec2_only_monthly * 36,
                    'eks_monthly': eks_costs.get('monthly_avg', 0),
                    'eks_annual': eks_costs.get('monthly_avg', 0) * 12,
                    'eks_3yr': eks_costs.get('total_3yr', 0),
                    'total_monthly': ec2_only_monthly + eks_costs.get('monthly_avg', 0),
                    'total_annual': (ec2_only_monthly + eks_costs.get('monthly_avg', 0)) * 12,
                    'total_3yr': (ec2_only_monthly * 36) + eks_costs.get('total_3yr', 0),
                    'ec2_vm_count': ec2_only_vm_count,
                    'eks_vm_count': len(eks_vms),
                    'ec2_cost_map': ec2_cost_map_all_vms if 'ec2_cost_map_all_vms' in locals() else {}
                }
                
                logger.info(f"  - EC2 only ({ec2_only_vm_count} VMs): ${ec2_only_monthly:,.2f}/month")
                logger.info(f"  - EKS ({len(eks_vms)} VMs): ${eks_costs.get('monthly_avg', 0):,.2f}/month")
                logger.info(f"  - Hybrid total: ${hybrid_costs['total_monthly']:,.2f}/month")
                
                # Add hybrid costs to EKS results
                eks_results['hybrid_costs'] = hybrid_costs
                
                # ============================================================================
                # CALCULATE DETERMINISTIC RECOMMENDATION (Python-based, not LLM)
                # ============================================================================
                logger.info("Calculating EKS recommendation using deterministic logic...")
                
                # Extract Option 1 and Option 2 costs from Excel file
                try:
                    import pandas as pd
                    import glob
                    from agents.utils.project_context import get_case_id
                    
                    # Try to find Excel file in case-specific output folder first
                    case_id = get_case_id()
                    excel_files = []
                    
                    logger.info(f"[EKS Recommendation] Looking for Excel file - case_id: {case_id}, output_dir: {output_folder_dir_path}")
                    
                    if case_id:
                        case_output_dir = os.path.join(output_folder_dir_path, case_id)
                        logger.info(f"[EKS Recommendation] Checking case-specific directory: {case_output_dir}")
                        if os.path.exists(case_output_dir):
                            # Try IT Inventory in case folder
                            excel_files = glob.glob(os.path.join(case_output_dir, 'it_inventory_aws_pricing_*.xlsx'))
                            
                            # If no IT Inventory, try RVTools in case folder
                            if not excel_files:
                                rvtools_path = os.path.join(case_output_dir, 'vm_to_ec2_mapping.xlsx')
                                logger.info(f"[EKS Recommendation] Checking RVTools path: {rvtools_path}")
                                if os.path.exists(rvtools_path):
                                    excel_files = [rvtools_path]
                                    logger.info(f"[EKS Recommendation] ✓ Found RVTools file in case directory")
                                else:
                                    logger.warning(f"[EKS Recommendation] RVTools file not found at: {rvtools_path}")
                        else:
                            logger.warning(f"[EKS Recommendation] Case output directory does not exist: {case_output_dir}")
                    
                    # Fallback to root output folder if not found in case folder
                    if not excel_files:
                        # Try IT Inventory first
                        excel_files = glob.glob(os.path.join(output_folder_dir_path, 'it_inventory_aws_pricing_*.xlsx'))
                        
                        # If no IT Inventory, try RVTools
                        if not excel_files:
                            rvtools_path = os.path.join(output_folder_dir_path, 'vm_to_ec2_mapping.xlsx')
                            if os.path.exists(rvtools_path):
                                excel_files = [rvtools_path]
                    
                    if excel_files:
                        latest_excel = max(excel_files, key=os.path.getmtime)
                        df = pd.read_excel(latest_excel, sheet_name='Pricing_Comparison' if 'it_inventory' in os.path.basename(latest_excel) else 'Pricing Comparison')
                        values = df['Value'].tolist()
                        
                        # Extract Option 1 and Option 2 costs
                        if 'it_inventory' in os.path.basename(latest_excel):
                            # IT Inventory format - Extract EC2, RDS, and Total separately
                            option1_ec2_monthly = float(str(values[4]).replace('$', '').replace(',', '')) if len(values) > 4 else 0
                            option1_rds_monthly = float(str(values[5]).replace('$', '').replace(',', '')) if len(values) > 5 else 0
                            option1_monthly = float(str(values[6]).replace('$', '').replace(',', '')) if len(values) > 6 else 2676.60
                            option1_3yr = float(str(values[10]).replace('$', '').replace(',', '')) if len(values) > 10 else option1_monthly * 36
                            option2_ec2_monthly = float(str(values[13]).replace('$', '').replace(',', '')) if len(values) > 13 else 0
                            option2_rds_monthly = float(str(values[14]).replace('$', '').replace(',', '')) if len(values) > 14 else 0
                            option2_monthly = float(str(values[15]).replace('$', '').replace(',', '')) if len(values) > 15 else 2973.65
                            option2_3yr = float(str(values[19]).replace('$', '').replace(',', '')) if len(values) > 19 else option2_monthly * 36
                            
                            # Store RDS costs for hybrid calculation
                            has_rds = option1_rds_monthly > 0
                            rds_monthly = option1_rds_monthly
                            rds_annual = rds_monthly * 12
                            rds_3yr = option1_3yr - (option1_ec2_monthly * 36)  # Extract RDS 3yr including upfront
                        else:
                            # RVTools format - No RDS
                            # NEW STRUCTURE (with backup costs):
                            # Row 5: Total Monthly (incl. Backup)
                            # Row 7: 3-Year Pricing
                            # Row 12: Option 2 Total Monthly
                            # Row 14: Option 2 3-Year
                            option1_monthly = float(str(values[5]).replace('$', '').replace(',', '')) if len(values) > 5 else 3101.65
                            option1_3yr = float(str(values[7]).replace('$', '').replace(',', '')) if len(values) > 7 else option1_monthly * 36
                            option2_monthly = float(str(values[12]).replace('$', '').replace(',', '')) if len(values) > 12 else 3398.70
                            option2_3yr = float(str(values[14]).replace('$', '').replace(',', '')) if len(values) > 14 else option2_monthly * 36
                            
                            # No RDS for RVTools
                            has_rds = False
                            rds_monthly = 0
                            rds_annual = 0
                            rds_3yr = 0
                    else:
                        # Fallback values - No RDS
                        option1_monthly = 2676.60
                        option1_3yr = option1_monthly * 36
                        option2_monthly = 2973.65
                        option2_3yr = option2_monthly * 36
                        has_rds = False
                        rds_monthly = 0
                        rds_annual = 0
                        rds_3yr = 0
                        
                except Exception as e:
                    logger.warning(f"Could not extract Option 1/2 costs from Excel: {e}")
                    # Fallback values - No RDS
                    option1_monthly = 2676.60
                    option1_3yr = option1_monthly * 36
                    option2_monthly = 2973.65
                    option2_3yr = option2_monthly * 36
                    has_rds = False
                    rds_monthly = 0
                    rds_annual = 0
                    rds_3yr = 0
                
                # CRITICAL FIX: Calculate backup costs for hybrid scenario
                # Hybrid backup should be: EC2 VMs (30) + EKS VMs (21), NOT all VMs (51)
                logger.info("=" * 60)
                logger.info("CALCULATING HYBRID BACKUP COSTS (EC2 + EKS SPLIT)")
                logger.info("=" * 60)
                backup_monthly = 0
                backup_3yr = 0
                ec2_backup_monthly = 0
                eks_backup_monthly = eks_costs.get('backup_monthly', 0)
                logger.info(f"EKS backup from eks_costs: ${eks_backup_monthly:,.2f}/month")
                
                # Calculate EC2-only backup costs (for VMs staying on EC2)
                try:
                    from agents.pricing.backup_pricing import calculate_backup_costs
                    
                    logger.info(f"ec2_only_vms count: {len(ec2_only_vms)}")
                    logger.info(f"eks_vms count: {len(eks_vms)}")
                    logger.info(f"vms_for_eks count: {len(vms_for_eks)}")
                    
                    # Get only EC2 VMs (not going to EKS)
                    ec2_only_vms_for_backup = []
                    for vm in vms_for_eks:
                        vm_name = vm.get('name', vm.get('vm_name', ''))
                        # Check if this VM is in the EC2 list (not EKS)
                        is_ec2 = any(ec2_vm.get('name', ec2_vm.get('vm_name', '')) == vm_name for ec2_vm in ec2_only_vms)
                        if is_ec2:
                            ec2_only_vms_for_backup.append(vm)
                    
                    logger.info(f"EC2 VMs for backup: {len(ec2_only_vms_for_backup)}")
                    
                    if ec2_only_vms_for_backup:
                        logger.info("Calling calculate_backup_costs for EC2 VMs...")
                        ec2_backup_result = calculate_backup_costs(ec2_only_vms_for_backup, target_region)
                        ec2_backup_monthly = ec2_backup_result.get('total_monthly', 0)
                        logger.info(f"  - EC2 backup ({len(ec2_only_vms_for_backup)} VMs): ${ec2_backup_monthly:,.2f}/month")
                    else:
                        logger.warning("No EC2 VMs found for backup calculation!")
                    
                    # Total hybrid backup = EC2 backup + EKS backup
                    backup_monthly = ec2_backup_monthly + eks_backup_monthly
                    backup_3yr = backup_monthly * 36
                    
                    logger.info(f"  - EKS backup ({len(eks_vms)} VMs): ${eks_backup_monthly:,.2f}/month")
                    logger.info(f"  - Total hybrid backup: ${backup_monthly:,.2f}/month")
                    logger.info("=" * 60)
                    
                except Exception as e:
                    logger.warning(f"Could not calculate hybrid backup costs: {e}")
                    # Fallback: Use Option 1 backup cost (all VMs)
                    try:
                        if is_rvtools:
                            backup_monthly = float(str(values[4]).replace('$', '').replace(',', '')) if len(values) > 4 else 0
                        else:
                            # IT Inventory: Extract from pricing comparison
                            pass
                        backup_3yr = backup_monthly * 36
                        logger.warning(f"  - Using fallback backup cost: ${backup_monthly:,.2f}/month (all VMs)")
                    except:
                        backup_monthly = 0
                        backup_3yr = 0
                
                # CRITICAL FIX: Add RDS costs to hybrid calculation for IT Inventory
                # NOTE: eks_monthly already includes EKS backup, so only add EC2 backup here
                if has_rds:
                    logger.info(f"  - RDS (14 databases): ${rds_monthly:,.2f}/month")
                    logger.info(f"  - Backup: ${backup_monthly:,.2f}/month (EC2: ${ec2_backup_monthly:,.2f}, EKS: ${eks_backup_monthly:,.2f})")
                    logger.info(f"  - NOTE: EKS backup already included in eks_monthly, only adding EC2 backup")
                    logger.info(f"  - Hybrid total (EC2 + EKS + RDS + EC2 Backup): ${hybrid_costs['ec2_monthly'] + hybrid_costs['eks_monthly'] + rds_monthly + ec2_backup_monthly:,.2f}/month")
                    
                    # Update hybrid costs to include RDS and EC2 backup only (EKS backup already in eks_monthly)
                    hybrid_costs['rds_monthly'] = rds_monthly
                    hybrid_costs['rds_annual'] = rds_annual
                    hybrid_costs['rds_3yr'] = rds_3yr
                    hybrid_costs['backup_monthly'] = backup_monthly  # Keep full backup for reporting
                    hybrid_costs['backup_3yr'] = backup_3yr
                    hybrid_costs['ec2_backup_monthly'] = ec2_backup_monthly
                    hybrid_costs['eks_backup_monthly'] = eks_backup_monthly
                    # FIX: Only add ec2_backup_monthly since eks_backup is already in eks_monthly
                    hybrid_costs['total_monthly'] = hybrid_costs['ec2_monthly'] + hybrid_costs['eks_monthly'] + rds_monthly + ec2_backup_monthly
                    hybrid_costs['total_annual'] = hybrid_costs['total_monthly'] * 12
                    hybrid_costs['total_3yr'] = hybrid_costs['ec2_3yr'] + hybrid_costs['eks_3yr'] + rds_3yr + (ec2_backup_monthly * 36)
                    hybrid_costs['has_rds'] = True
                else:
                    logger.info(f"  - Backup: ${backup_monthly:,.2f}/month (EC2: ${ec2_backup_monthly:,.2f}, EKS: ${eks_backup_monthly:,.2f})")
                    logger.info(f"  - NOTE: EKS backup already included in eks_monthly, only adding EC2 backup")
                    logger.info(f"  - Hybrid total (EC2 + EKS + EC2 Backup): ${hybrid_costs['ec2_monthly'] + hybrid_costs['eks_monthly'] + ec2_backup_monthly:,.2f}/month")
                    
                    # Update hybrid costs to include EC2 backup only (EKS backup already in eks_monthly)
                    hybrid_costs['rds_monthly'] = 0
                    hybrid_costs['rds_annual'] = 0
                    hybrid_costs['rds_3yr'] = 0
                    hybrid_costs['backup_monthly'] = backup_monthly  # Keep full backup for reporting
                    hybrid_costs['backup_3yr'] = backup_3yr
                    hybrid_costs['ec2_backup_monthly'] = ec2_backup_monthly
                    hybrid_costs['eks_backup_monthly'] = eks_backup_monthly
                    # FIX: Only add ec2_backup_monthly since eks_backup is already in eks_monthly
                    hybrid_costs['total_monthly'] = hybrid_costs['ec2_monthly'] + hybrid_costs['eks_monthly'] + ec2_backup_monthly
                    hybrid_costs['total_annual'] = hybrid_costs['total_monthly'] * 12
                    hybrid_costs['total_3yr'] = hybrid_costs['ec2_3yr'] + hybrid_costs['eks_3yr'] + (ec2_backup_monthly * 36)
                    hybrid_costs['has_rds'] = False
                
                # Calculate recommendation using deterministic Python logic
                recommendation = calculate_eks_recommendation(
                    option1_3yr=option1_3yr,
                    option2_3yr=option2_3yr,
                    option3_3yr=hybrid_costs['total_3yr'],
                    option1_monthly=option1_monthly,
                    option2_monthly=option2_monthly,
                    option3_monthly=hybrid_costs['total_monthly'],
                    ec2_vm_count=ec2_only_vm_count,
                    eks_vm_count=len(eks_vms)
                )
                
                # Log the recommendation decision
                logger.info("="*80)
                logger.info("EKS RECOMMENDATION (DETERMINISTIC)")
                logger.info("="*80)
                logger.info(f"Tier: {recommendation['tier_name']}")
                logger.info(f"Recommended: {recommendation['recommended_option_name']}")
                logger.info(f"Cost Difference: ${recommendation['cost_difference_dollars']:,.2f} ({recommendation['cost_difference_pct']:.2f}%)")
                logger.info(f"Rationale: {recommendation['rationale_template']}")
                logger.info("="*80)
                
                # Add recommendation to EKS results
                eks_results['recommendation'] = recommendation
                
                # Step 7: Generate EKS Excel export
                logger.info("Step 7: Generating EKS Excel export...")
                try:
                    # Calculate ACTUAL EC2 costs for EKS VMs (not estimate)
                    # Use AWS pricing calculator to get real EC2 costs for these specific VMs
                    from agents.pricing.aws_pricing_calculator import AWSPricingCalculator
                    calculator = AWSPricingCalculator(region=target_region)
                    
                    ec2_monthly_for_eks_vms = 0
                    for vm in eks_vms:
                        # Get EC2 instance recommendation for this VM
                        vcpu = vm.get('vcpu', 2)
                        memory_gb = vm.get('memory', vm.get('memory_gb', 4))
                        os_type = 'Windows' if 'windows' in vm.get('os', '').lower() else 'Linux'
                        
                        # Get recommended instance type (correct method name)
                        instance_type = calculator.map_vm_to_instance_type(vcpu, memory_gb, os_type)
                        
                        # Get 3-year RI pricing (correct method and parameters)
                        hourly_rate = calculator.get_ec2_price_by_term(
                            instance_type=instance_type,
                            os_type=os_type,
                            region=target_region,
                            term='3yr',
                            purchase_option='No Upfront'
                        )
                        
                        # Add monthly cost
                        ec2_monthly_for_eks_vms += hourly_rate * 730  # 730 hours/month
                    
                    # Add storage costs for EKS VMs
                    total_storage_gb = sum(vm.get('storage_gb', 100) for vm in eks_vms)
                    ebs_rate = calculator.get_ebs_gp3_price(target_region)
                    storage_monthly = total_storage_gb * ebs_rate
                    
                    ec2_costs_for_comparison = {
                        'total_monthly': ec2_monthly_for_eks_vms + storage_monthly,
                        'compute_monthly': ec2_monthly_for_eks_vms,
                        'storage_monthly': storage_monthly,
                        'data_transfer_monthly': 0,  # Minimal for comparison
                        'total_annual': (ec2_monthly_for_eks_vms + storage_monthly) * 12,
                        'total_3yr': (ec2_monthly_for_eks_vms + storage_monthly) * 36
                    }
                    
                    logger.info(f"  - EC2 cost for {len(eks_vms)} EKS VMs (3yr RI): ${ec2_costs_for_comparison['total_monthly']:,.2f}/month")
                    logger.info(f"  - EKS cost for {len(eks_vms)} VMs: ${eks_costs.get('monthly_avg', 0):,.2f}/month")
                    
                    eks_excel_path = export_eks_analysis_to_excel(
                        vm_categorization=vm_categorization,
                        cluster_config=cluster_config,
                        ec2_costs=ec2_costs_for_comparison,
                        eks_costs=eks_costs,
                        strategy_recommendation=strategy_recommendation,
                        mra_scores=mra_scores,
                        backup_costs=hybrid_costs,  # Pass hybrid costs with EC2/EKS backup split
                        output_filename='eks_migration_analysis.xlsx',
                        region=target_region
                    )
                    logger.info(f"✓ EKS Excel generated: {eks_excel_path}")
                except Exception as e:
                    logger.error(f"Failed to generate EKS Excel: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                
                logger.info("✓ EKS analysis completed successfully")
                logger.info(f"✓ EKS results available: {eks_results is not None}")
                if eks_results:
                    logger.info(f"✓ EKS Excel path: {eks_results.get('excel_path', 'N/A')}")
            else:
                logger.info("No VMs eligible for EKS migration")
        else:
            logger.info("No VM data available for EKS analysis")
            
    except Exception as e:
        logger.error(f"EKS analysis failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        eks_results = None
else:
    logger.info("✗ EKS analysis skipped (disabled or strategy='disabled')")

logger.info("="*80)
logger.info("FINAL BUSINESS CASE GENERATION")
logger.info("="*80)

# Check if multi-stage generation is enabled
if ENABLE_MULTI_STAGE:
    logger.info("Using MULTI-STAGE generation for comprehensive business case")
    try:
        # Add timeline requirement to project context for multi-stage generation
        project_context_with_timeline = project_context
        if timeline_months:
            project_context_with_timeline = f"""{project_context}

**⚠️ CRITICAL - MIGRATION TIMELINE REQUIREMENT: {timeline_months} MONTHS ⚠️**
**ALL migration phases, waves, and timelines MUST fit within {timeline_months} months total.**
**DO NOT exceed {timeline_months} months under any circumstances.**
**Example for {timeline_months} months: Phase 1 (Months 1-{timeline_months//3}) + Phase 2 (Months {timeline_months//3+1}-{timeline_months*2//3}) + Phase 3 (Months {timeline_months*2//3+1}-{timeline_months}) = {timeline_months} months**
"""
        
        # Check if this is ATX PowerPoint-only (no Excel file)
        # If so, use deterministic generation instead of LLM
        if has_atx_pptx and not has_it_inventory and not has_rvtools:
            logger.info("="*60)
            logger.info("ATX PowerPoint-only detected - using deterministic generation")
            logger.info("="*60)
            
            try:
                from agents.core.atx_business_case_generator import generate_atx_business_case
                
                # Get ATX PowerPoint path
                if 'atxPpt' in uploaded_files:
                    atx_ppt_filename = uploaded_files['atxPpt']
                else:
                    atx_ppt_filename = "atx_business_case.pptx"
                
                atx_ppt_path = os.path.join(project_root, input_base, atx_ppt_filename)
                
                # Extract project context dict
                project_dict = {
                    'customer_name': customer_name,
                    'project_name': project_name,
                    'target_region': target_region,
                    'timeline_months': timeline_months
                }
                
                # Generate deterministic business case
                final_result_text = generate_atx_business_case(atx_ppt_path, project_dict)
                logger.info(f"Deterministic ATX business case generated ({len(final_result_text)} characters)")
                
            except Exception as e:
                logger.warning(f"Deterministic ATX generation failed: {e}")
                logger.info("Falling back to multi-stage LLM generation")
                final_result_text = generate_multi_stage_business_case(result.results, project_context_with_timeline, rvtools_summary, it_inventory_summary, atx_summary, mra_summary if mra_content else None)
                logger.info(f"Multi-stage business case generated ({len(final_result_text)} characters)")
        else:
            # Add EKS results to agent results if available
            if eks_results:
                result.results['eks_analysis'] = type('obj', (object,), {
                    'result': eks_results,
                    'status': Status.COMPLETED
                })()
                logger.info("✓ EKS results added to business case context")
            
            # Generate business case in multiple stages using LLM
            final_result_text = generate_multi_stage_business_case(result.results, project_context_with_timeline, rvtools_summary, it_inventory_summary, atx_summary, mra_summary if mra_content else None)
            logger.info(f"Multi-stage business case generated ({len(final_result_text)} characters)")
        
        file_path = os.path.join(output_folder_dir_path, 'aws_business_case.md')
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(final_result_text)
        logger.info(f"Business case saved to: {file_path}")
        
    except Exception as e:
        logger.error(f"Multi-stage generation failed: {str(e)}")
        logger.info("Falling back to single-stage generation")
        
        if "aws_business_case" in result.results:
            final_result = result.results["aws_business_case"].result
            final_result_text = str(final_result)
            file_path = os.path.join(output_folder_dir_path, 'aws_business_case.md')
            with open(file_path, "w", encoding="utf-8") as file:
                file.write("# AWS Business Case Report\n\n")
                file.write(f"Generated on: {result.execution_order[-1].execution_time}ms execution time\n\n")
                file.write("---\n\n")
                file.write(final_result_text)
            logger.info(f"Business case saved to: {file_path}")
else:
    logger.info("Using SINGLE-STAGE generation")
    if "aws_business_case" in result.results:
        final_result = result.results["aws_business_case"].result
        logger.info("Business case generated successfully")
        
        final_result_text = str(final_result)
        file_path = os.path.join(output_folder_dir_path, 'aws_business_case.md')
        with open(file_path, "w", encoding="utf-8") as file:
            file.write("# AWS Business Case Report\n\n")
            file.write(f"Generated on: {result.execution_order[-1].execution_time}ms execution time\n\n")
            file.write("---\n\n")
            file.write(final_result_text)
            file.write("\n\n---\n\n")
            file.write(get_appendix())
        logger.info(f"Business case saved to: {file_path}")
    else:
        logger.error("Business case not found in results")

logger.info("="*60)
logger.info(f"Status: {result.status}")
logger.info(f"Execution order: {[node.node_id for node in result.execution_order]}")
logger.info("="*60)

# Display overall performance
logger.info("=== Graph Performance ===")
logger.info(f"Total Nodes Executed: {result.completed_nodes}/{result.total_nodes}")
logger.info(f"Total Execution Time: {result.execution_time}ms")
logger.info(f"Token Usage: {result.accumulated_usage}")

# Display individual node performance
logger.info("=== Individual Node Performance ===")
for node in result.execution_order:
    logger.info(f"- {node.node_id}: {node.execution_time}ms")
    if hasattr(node, 'status'):
        logger.info(f"  Status: {node.status}")

logger.info("="*80)
logger.info(f"Log file saved to: {log_file}")
logger.info("="*80)

# ================================================================================
# CASE OUTPUT SUMMARY
# ================================================================================
logger.info("="*80)
logger.info("CASE OUTPUT SUMMARY")
logger.info("="*80)

if case_manager:
    try:
        # Display case summary
        summary = case_manager.get_case_summary()
        logger.info(f"Case ID: {summary['case_id']}")
        logger.info(f"Output Directory: {summary['output_directory']}")
        logger.info(f"Total Files: {summary['file_count']}")
        logger.info(f"Files: {', '.join(summary['files'])}")
        logger.info("")
        logger.info("NOTE: All files saved directly to case-specific folder")
        logger.info("      S3 upload is handled by UI backend (ui/backend/app.py)")
        logger.info("="*80)
    except Exception as e:
        logger.error(f"Could not generate case summary: {e}")
else:
    logger.warning("Case manager not initialized - files may be in root output folder")
    logger.warning("Manual organization may be required")



