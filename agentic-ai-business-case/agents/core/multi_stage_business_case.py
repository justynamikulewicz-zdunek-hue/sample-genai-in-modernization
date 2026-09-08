"""
Multi-stage business case generator
Generates business case in sections to maximize quality and detail
"""
import os
import pandas as pd
import glob
from datetime import datetime
from strands import Agent
from strands.models import BedrockModel
from agents.config.config import (
    model_id_claude3_7, 
    model_temperature, 
    MAX_TOKENS_BUSINESS_CASE, 
    output_folder_dir_path,
    TCO_COMPARISON_CONFIG,
    EKS_CONFIG
)
from agents.export.appendix_content import get_appendix


def get_migration_timeline():
    """
    Get migration timeline in months from project context.
    Returns consistent timeline across all sections.
    
    Priority:
    1. Explicit migrationTimeline in project_info
    2. Extract from projectDescription
    3. Default to 18 months (matches wave planning default)
    """
    from agents.utils.project_context import get_project_info_dict
    project_info = get_project_info_dict()
    
    # Check if explicitly set
    if 'migrationTimeline' in project_info:
        return project_info['migrationTimeline']
    
    # Extract from project description
    if 'projectDescription' in project_info:
        desc = project_info['projectDescription'].lower()
        import re
        # Look for patterns like "12 months", "18-month", "24 month"
        timeline_match = re.search(r'(\d+)[\s-]?months?', desc)
        if timeline_match:
            return int(timeline_match.group(1))
    
    # Default to 18 months (matches wave planning default)
    print("⚠ Timeline not found in project description, using default: 18 months")
    return 18


def extract_exact_costs_from_excel(agent_results=None):
    """
    Extract exact cost numbers from the Excel file to prevent LLM hallucination
    Handles both IT Inventory and RVTools Excel files
    Returns formatted string with exact costs to inject into context
    
    Args:
        agent_results: Dictionary of agent results from graph execution (optional)
                      Used to extract EKS analysis results if available
    """
    try:
        # Try to find Excel file in case-specific output folder first
        from agents.utils.project_context import get_case_id
        case_id = get_case_id()
        
        excel_files = []  # Initialize to empty list
        
        if case_id:
            case_output_dir = os.path.join(output_folder_dir_path, case_id)
            if os.path.exists(case_output_dir):
                # Try IT Inventory in case folder
                excel_files = glob.glob(os.path.join(case_output_dir, 'it_inventory_aws_pricing_*.xlsx'))
                
                # If no IT Inventory, try RVTools in case folder
                if not excel_files:
                    rvtools_path = os.path.join(case_output_dir, 'vm_to_ec2_mapping.xlsx')
                    if os.path.exists(rvtools_path):
                        excel_files = [rvtools_path]
        
        # Fallback to root output folder if not found in case folder
        if not excel_files:
            # Try IT Inventory first
            excel_files = glob.glob(os.path.join(output_folder_dir_path, 'it_inventory_aws_pricing_*.xlsx'))
            
            # If no IT Inventory, try RVTools
            if not excel_files:
                rvtools_path = os.path.join(output_folder_dir_path, 'vm_to_ec2_mapping.xlsx')
                if os.path.exists(rvtools_path):
                    excel_files = [rvtools_path]
        
        if not excel_files:
            return None
        
        latest_excel = max(excel_files, key=os.path.getmtime)
        
        # Determine file type
        is_it_inventory = 'it_inventory' in os.path.basename(latest_excel)
        
        # Read the Pricing_Comparison sheet
        df = pd.read_excel(latest_excel, sheet_name='Pricing_Comparison' if is_it_inventory else 'Pricing Comparison')
        
        # Extract values (they're in the 'Value' column)
        values = df['Value'].tolist()
        
        if is_it_inventory:
            # IT Inventory format (EC2 + RDS)
            # Parse ALL the values from the Pricing_Comparison sheet
            # Row 0: Total Servers
            # Row 1: Total Databases
            # Row 4: Option 1 EC2 Monthly Cost
            # Row 5: Option 1 RDS Monthly Cost
            # Row 6: Option 1 Total Monthly Cost
            # Row 7: Option 1 Total Annual Cost
            # Row 8: Option 1 3-Year Total Cost
            # Row 9: Option 1 RDS Upfront Fees
            # Row 12: Option 2 EC2 Monthly Cost
            # Row 13: Option 2 RDS Monthly Cost
            # Row 14: Option 2 Total Monthly Cost
            # Row 15: Option 2 Total Annual Cost
            # Row 16: Option 2 3-Year Total Cost
            # Row 17: Option 2 RDS Upfront Fees
            # Row 20: EC2 Monthly Savings
            # Row 21: RDS Monthly Savings
            # Row 22: Total Monthly Savings
            # Row 23: Annual Savings
            # Row 24: 3-Year Savings
            # Row 25: Savings Percentage
            
            total_servers = values[0] if len(values) > 0 else None
            total_databases = values[1] if len(values) > 1 else None
            
            opt1_ec2_monthly = values[4] if len(values) > 4 else None
            opt1_rds_monthly = values[5] if len(values) > 5 else None
            opt1_total_monthly = values[6] if len(values) > 6 else None
            opt1_annual = values[7] if len(values) > 7 else None
            opt1_3year = values[8] if len(values) > 8 else None
            opt1_rds_upfront = values[9] if len(values) > 9 else None
            opt1_3year_incl_upfront = values[10] if len(values) > 10 else None
            
            opt2_ec2_monthly = values[13] if len(values) > 13 else None
            opt2_rds_monthly = values[14] if len(values) > 14 else None
            opt2_total_monthly = values[15] if len(values) > 15 else None
            opt2_annual = values[16] if len(values) > 16 else None
            opt2_3year = values[17] if len(values) > 17 else None
            opt2_rds_upfront = values[18] if len(values) > 18 else None
            opt2_3year_incl_upfront = values[19] if len(values) > 19 else None
            
            ec2_savings = values[22] if len(values) > 22 else None
            rds_savings = values[23] if len(values) > 23 else None
            total_savings = values[24] if len(values) > 24 else None
            annual_savings = values[25] if len(values) > 25 else None
            three_year_savings = values[26] if len(values) > 26 else None
            three_year_savings_incl_upfront = values[27] if len(values) > 27 else None
            savings_pct = values[28] if len(values) > 28 else None
            
            # Extract storage and backup costs from Excel if available
            storage_compute_breakdown = ""
            backup_details = ""
            
            try:
                # Try to read EC2_Option1 sheet for storage details
                df_ec2 = pd.read_excel(latest_excel, sheet_name='EC2_Option1_Instance_SP')
                total_storage_gb = df_ec2['Optimized Storage (GB)'].sum()
                
                # Estimate storage cost (gp3 pricing ~$0.08/GB/month)
                storage_monthly = total_storage_gb * 0.08
                ec2_monthly_float = float(str(opt1_ec2_monthly).replace('$', '').replace(',', ''))
                compute_monthly = ec2_monthly_float - storage_monthly
                
                storage_compute_breakdown = f"""
STORAGE & COMPUTE BREAKDOWN (Option 1 EC2):
  Compute (EC2 Instances): ${compute_monthly:,.2f}/month
  Storage (EBS gp3): ${storage_monthly:,.2f}/month ({total_storage_gb:,.0f} GB)
  Total EC2: {opt1_ec2_monthly}
"""
            except Exception as e:
                print(f"Warning: Could not extract storage breakdown: {e}")
            
            try:
                # Try to extract backup costs from pricing results
                # Backup costs are included in the total but we can show the breakdown
                total_monthly_float = float(str(opt1_total_monthly).replace('$', '').replace(',', ''))
                ec2_float = float(str(opt1_ec2_monthly).replace('$', '').replace(',', ''))
                rds_float = float(str(opt1_rds_monthly).replace('$', '').replace(',', ''))
                
                # Backup is included in total, calculate it
                backup_monthly_estimate = total_monthly_float - ec2_float - rds_float
                
                if backup_monthly_estimate > 0:
                    # Estimate production vs pre-production split (typically 60/40 for IT inventory)
                    prod_count = int(total_servers * 0.6)
                    preprod_count = total_servers - prod_count
                    prod_backup = backup_monthly_estimate * 0.6
                    preprod_backup = backup_monthly_estimate * 0.4
                    
                    backup_details = f"""

**AWS BACKUP COSTS (INCLUDED IN ARR ABOVE)**:
  Total Backup Monthly: ${backup_monthly_estimate:,.2f}
  
  Production VMs ({prod_count} servers): ${prod_backup:,.2f}/month
    - Retention Policy: Daily 7d, Weekly 2w, Monthly 1yr
    - Lifecycle: Warm → Glacier (30d) → Deep Archive (90d)
    - Tiering Savings: 85% cost reduction vs warm storage
  
  Pre-Production VMs ({preprod_count} servers): ${preprod_backup:,.2f}/month
    - Retention Policy: Daily 3d, Weekly 1w (no monthly)
    - Lifecycle: Warm → Glacier (7d)
    - Tiering Savings: 70% cost reduction vs warm storage
  
  **IMPORTANT**: Backup costs are INCLUDED in "Total Monthly Cost" and "Total Annual Cost (ARR)" above.
  They are NOT additional costs - this is just a breakdown showing where backup costs are allocated.
"""
            except Exception as e:
                print(f"Warning: Could not extract backup details: {e}")
            
            # Calculate migration ramp costs
            timeline_months = get_migration_timeline()
            
            # Calculate migration ramp
            migration_ramp = calculate_migration_ramp(opt1_total_monthly, timeline_months)
            
            # Check if EKS analysis is available and extract Option 3 with RDS
            eks_option3_section = ""
            if agent_results and 'eks_analysis' in agent_results:
                try:
                    eks_result = agent_results['eks_analysis']
                    if hasattr(eks_result, 'result'):
                        eks_data = eks_result.result
                        
                        # Extract hybrid costs and recommendation
                        hybrid_costs = eks_data.get('hybrid_costs', {})
                        recommendation = eks_data.get('recommendation', {})
                        
                        if hybrid_costs and recommendation:
                            # Get all cost components
                            ec2_monthly = hybrid_costs.get('ec2_monthly', 0)
                            eks_monthly = hybrid_costs.get('eks_monthly', 0)
                            rds_monthly = hybrid_costs.get('rds_monthly', 0)
                            has_rds = hybrid_costs.get('has_rds', False)
                            
                            # Add RDS upfront fees for Option 3 (use Option 1 RDS upfront)
                            rds_upfront = opt1_rds_upfront if has_rds else 0
                            
                            hybrid_monthly = hybrid_costs.get('total_monthly', 0)
                            hybrid_annual = hybrid_costs.get('total_annual', 0)
                            hybrid_3yr = hybrid_costs.get('total_3yr', 0)
                            hybrid_3yr_with_upfront = hybrid_3yr + rds_upfront
                            ec2_vm_count = hybrid_costs.get('ec2_vm_count', 0)
                            eks_vm_count = hybrid_costs.get('eks_vm_count', 0)
                            
                            # Get recommendation details
                            recommended_option = recommendation.get('recommended_option_name', '')
                            cost_diff = recommendation.get('cost_difference_dollars', 0)
                            cost_diff_pct = recommendation.get('cost_difference_pct', 0)
                            rationale = recommendation.get('rationale_text', '')
                            
                            # Calculate Option 3 migration ramp
                            migration_ramp_option3 = calculate_migration_ramp(hybrid_monthly, timeline_months)
                            
                            # Build Option 3 section with RDS if applicable
                            rds_line = f"  RDS Monthly Cost: ${rds_monthly:,.2f}\n" if has_rds else ""
                            rds_upfront_line = f"  RDS Upfront Fees (One-time): ${rds_upfront:,.2f}\n" if has_rds and rds_upfront > 0 else ""
                            
                            eks_option3_section = f"""

OPTION 3: Hybrid (EC2 + EKS{' + RDS' if has_rds else ''}) - {'RECOMMENDED' if 'Option 3' in recommended_option else 'AVAILABLE'}
  EC2 Monthly ({ec2_vm_count} VMs): ${ec2_monthly:,.2f}
  EKS Monthly ({eks_vm_count} VMs): ${eks_monthly:,.2f}
{rds_line}{rds_upfront_line}  Total Monthly Cost: ${hybrid_monthly:,.2f}
  Total Annual Cost (ARR, including backup): ${hybrid_annual:,.2f}
  3-Year Pricing (Monthly × 36): ${hybrid_3yr:,.2f}
{f"  3-Year Total Cost (incl. upfront): ${hybrid_3yr_with_upfront:,.2f}" if has_rds and rds_upfront > 0 else ""}

EKS RECOMMENDATION (PRE-CALCULATED):
  Recommended Option: {recommended_option}
  Cost vs Option 1: ${cost_diff:,.2f} ({cost_diff_pct:+.2f}%)
  Rationale: {rationale}

HYBRID COST BREAKDOWN:
  EC2 Component: ${ec2_monthly:,.2f}/month × 36 = ${hybrid_costs.get('ec2_3yr', 0):,.2f}
  EKS Component: ${eks_monthly:,.2f}/month × 36 = ${hybrid_costs.get('eks_3yr', 0):,.2f}
{'  RDS Component: $' + f"{rds_monthly:,.2f}/month × 36 = ${hybrid_costs.get('rds_3yr', 0):,.2f}" if has_rds else ''}
{'  RDS Upfront: $' + f"{rds_upfront:,.2f} (one-time)" if has_rds and rds_upfront > 0 else ''}
  Total 3-Year: ${hybrid_3yr_with_upfront if has_rds and rds_upfront > 0 else hybrid_3yr:,.2f}

MIGRATION COST RAMP FOR OPTION 3 (PRE-CALCULATED):
{migration_ramp_option3}
"""
                except Exception as e:
                    print(f"Warning: Could not extract EKS Option 3 data: {e}")
            
            # Format the exact costs string with ALL details
            exact_costs = f"""
================================================================================
EXACT COSTS FROM EXCEL FILE (DO NOT MODIFY THESE NUMBERS)
================================================================================
Total Servers: {total_servers}
Total Databases: {total_databases}

OPTION 1: EC2 Instance SP (3yr) + RDS Partial Upfront (3yr)
  EC2 Monthly Cost: {opt1_ec2_monthly}
  RDS Monthly Cost: {opt1_rds_monthly}
  Total Monthly Cost: {opt1_total_monthly}
  Total Annual Cost (ARR, including backup): {opt1_annual}
  3-Year Pricing (Monthly × 36): {opt1_3year}
  RDS Upfront Fees (One-time): {opt1_rds_upfront}
  3-Year Total Cost (incl. upfront): {opt1_3year_incl_upfront}

OPTION 2: Compute SP (3yr) + RDS No Upfront (1yr × 3)
  EC2 Monthly Cost: {opt2_ec2_monthly}
  RDS Monthly Cost: {opt2_rds_monthly}
  Total Monthly Cost: {opt2_total_monthly}
  Total Annual Cost (ARR, including backup): {opt2_annual}
  3-Year Pricing (Monthly × 36): {opt2_3year}
  RDS Upfront Fees (One-time): {opt2_rds_upfront}
  3-Year Total Cost (incl. upfront): {opt2_3year_incl_upfront}
{eks_option3_section}
SAVINGS (Option 1 vs Option 2)
  EC2 Monthly Savings: {ec2_savings}
  RDS Monthly Savings: {rds_savings}
  Total Monthly Savings: {total_savings}
  Annual Savings: {annual_savings}
  3-Year Savings (monthly only): {three_year_savings}
  3-Year Savings (incl. upfront): {three_year_savings_incl_upfront}
  Savings Percentage: {savings_pct}
{storage_compute_breakdown}{backup_details}
MIGRATION COST RAMP FOR OPTION 1 (PRE-CALCULATED - USE EXACTLY AS SHOWN)
{migration_ramp}

**CRITICAL - WHICH COSTS TO USE IN EXECUTIVE SUMMARY**:
- Check "EKS RECOMMENDATION (PRE-CALCULATED)" section above
- IF "Recommended Option: Option 3" → Use Option 3 costs (Hybrid with RDS)
- IF "Recommended Option: Option 1" → Use Option 1 costs
- IF "Recommended Option: Option 2" → Use Option 2 costs
- IF no EKS recommendation → Use Option 1 costs (default)

**CRITICAL - RECOMMENDATION CONSISTENCY (MUST FOLLOW)**:
- ALL sections MUST use the SAME recommended option shown in "EKS RECOMMENDATION (PRE-CALCULATED)" above
- Key Financial Metrics section: Use the recommended option from above
- Cost Analysis section: Use the recommended option from above
- Executive Summary: Use the recommended option from above
- DO NOT calculate or change the recommendation - it is PRE-CALCULATED
- DO NOT show different recommendations in different sections
- If you see "Recommended Option: Option 3" above, then ALL sections must recommend Option 3
- If you see "Recommended Option: Option 1" above, then ALL sections must recommend Option 1

**CRITICAL - MIGRATION RAMP USAGE**:
- IF Option 1 or Option 2 is recommended: Use "MIGRATION COST RAMP FOR OPTION 1" above
- IF Option 3 (Hybrid) is recommended: Use "MIGRATION COST RAMP FOR OPTION 3" above
- The migration ramp is PRE-CALCULATED - copy it EXACTLY as shown

================================================================================
USE THESE EXACT NUMBERS IN THE COST ANALYSIS SECTION
DO NOT ROUND, MODIFY, OR ESTIMATE - COPY THEM EXACTLY AS SHOWN ABOVE
DO NOT MAKE UP ANY NUMBERS - ALL VALUES ARE PROVIDED ABOVE
THE MIGRATION COST RAMP IS PRE-CALCULATED - COPY IT EXACTLY
================================================================================
"""
        else:
            # RVTools format (EC2-only, no RDS)
            # Row 0: Total VMs
            # Row 3: Option 1 Total Monthly Cost
            # Row 4: Option 1 Total Annual Cost
            # Row 5: Option 1 3-Year Pricing
            # Row 8: Option 2 Total Monthly Cost
            # Row 9: Option 2 Total Annual Cost
            # Row 10: Option 2 3-Year Pricing
            # Row 13: Monthly Savings
            # Row 14: Annual Savings
            # Row 15: 3-Year Savings
            # Row 16: Savings Percentage
            
            total_vms = values[0] if len(values) > 0 else None
            
            opt1_total_monthly = values[3] if len(values) > 3 else None
            opt1_annual = values[4] if len(values) > 4 else None
            opt1_3year = values[5] if len(values) > 5 else None
            
            opt2_total_monthly = values[8] if len(values) > 8 else None
            opt2_annual = values[9] if len(values) > 9 else None
            opt2_3year = values[10] if len(values) > 10 else None
            
            total_savings = values[13] if len(values) > 13 else None
            annual_savings = values[14] if len(values) > 14 else None
            three_year_savings = values[15] if len(values) > 15 else None
            savings_pct = values[16] if len(values) > 16 else None
            
            # Calculate migration ramp costs
            timeline_months = get_migration_timeline()
            
            # Calculate migration ramp
            migration_ramp = calculate_migration_ramp(opt1_total_monthly, timeline_months)
            
            # Check if EKS analysis is available and Option 3 is recommended
            eks_option3_section = ""
            if agent_results and 'eks_analysis' in agent_results:
                try:
                    eks_result = agent_results['eks_analysis']
                    if hasattr(eks_result, 'result'):
                        eks_data = eks_result.result
                        
                        # Extract hybrid costs and recommendation
                        hybrid_costs = eks_data.get('hybrid_costs', {})
                        recommendation = eks_data.get('recommendation', {})
                        
                        if hybrid_costs and recommendation:
                            # Calculate migration ramp for Option 3
                            hybrid_monthly = hybrid_costs.get('total_monthly', 0)
                            hybrid_annual = hybrid_costs.get('total_annual', 0)
                            hybrid_3yr = hybrid_costs.get('total_3yr', 0)
                            ec2_vm_count = hybrid_costs.get('ec2_vm_count', 0)
                            eks_vm_count = hybrid_costs.get('eks_vm_count', 0)
                            
                            # Get recommendation details
                            recommended_option = recommendation.get('recommended_option_name', '')
                            cost_diff = recommendation.get('cost_difference_dollars', 0)
                            cost_diff_pct = recommendation.get('cost_difference_pct', 0)
                            rationale = recommendation.get('rationale_text', '')
                            
                            # Calculate Option 3 migration ramp
                            migration_ramp_option3 = calculate_migration_ramp(hybrid_monthly, timeline_months)
                            
                            eks_option3_section = f"""

OPTION 3: Hybrid (EC2 + EKS) - {'RECOMMENDED' if 'Option 3' in recommended_option else 'AVAILABLE'}
  EC2 Monthly ({ec2_vm_count} VMs): ${hybrid_costs.get('ec2_monthly', 0):,.2f}
  EKS Monthly ({eks_vm_count} VMs): ${hybrid_costs.get('eks_monthly', 0):,.2f}
  Total Monthly Cost: ${hybrid_monthly:,.2f}
  Total Annual Cost (ARR, including backup): ${hybrid_annual:,.2f}
  3-Year Total Cost: ${hybrid_3yr:,.2f}

EKS RECOMMENDATION (PRE-CALCULATED):
  Recommended Option: {recommended_option}
  Cost vs Option 1: ${cost_diff:,.2f} ({cost_diff_pct:+.2f}%)
  Rationale: {rationale}

HYBRID COST BREAKDOWN:
  EC2 Component: ${hybrid_costs.get('ec2_monthly', 0):,.2f}/month × 36 = ${hybrid_costs.get('ec2_3yr', 0):,.2f}
  EKS Component: ${hybrid_costs.get('eks_monthly', 0):,.2f}/month × 36 = ${hybrid_costs.get('eks_3yr', 0):,.2f}
  Total 3-Year: ${hybrid_3yr:,.2f}

MIGRATION COST RAMP FOR OPTION 3 (PRE-CALCULATED):
{migration_ramp_option3}
"""
                except Exception as e:
                    print(f"Warning: Could not extract EKS Option 3 data: {e}")
            
            # Format the exact costs string for RVTools (EC2-only)
            exact_costs = f"""
================================================================================
EXACT COSTS FROM EXCEL FILE (DO NOT MODIFY THESE NUMBERS)
================================================================================
Total VMs: {total_vms}

OPTION 1: 3-Year EC2 Instance Savings Plan
  Total Monthly Cost: {opt1_total_monthly}
  Total Annual Cost (ARR, including backup): {opt1_annual}
  3-Year Pricing: {opt1_3year}

OPTION 2: 3-Year Compute Savings Plan
  Total Monthly Cost: {opt2_total_monthly}
  Total Annual Cost (ARR, including backup): {opt2_annual}
  3-Year Pricing: {opt2_3year}
{eks_option3_section}
SAVINGS (Option 1 vs Option 2)
  Monthly Savings: {total_savings}
  Annual Savings: {annual_savings}
  3-Year Savings: {three_year_savings}
  Savings Percentage: {savings_pct}

MIGRATION COST RAMP FOR OPTION 1 (PRE-CALCULATED - USE EXACTLY AS SHOWN)
{migration_ramp}

**CRITICAL - WHICH COSTS TO USE IN EXECUTIVE SUMMARY**:
- Check "EKS RECOMMENDATION (PRE-CALCULATED)" section above
- IF "Recommended Option: Option 3" → Use Option 3 costs (Hybrid)
- IF "Recommended Option: Option 1" → Use Option 1 costs
- IF "Recommended Option: Option 2" → Use Option 2 costs
- IF no EKS recommendation → Use Option 1 costs (default)

**CRITICAL - MIGRATION RAMP USAGE**:
- IF Option 1 or Option 2 is recommended: Use "MIGRATION COST RAMP FOR OPTION 1" above
- IF Option 3 (Hybrid) is recommended: Use "MIGRATION COST RAMP FOR OPTION 3" above
- The migration ramp is PRE-CALCULATED - copy it EXACTLY as shown

================================================================================
USE THESE EXACT NUMBERS IN THE COST ANALYSIS SECTION
DO NOT ROUND, MODIFY, OR ESTIMATE - COPY THEM EXACTLY AS SHOWN ABOVE
DO NOT MAKE UP ANY NUMBERS - ALL VALUES ARE PROVIDED ABOVE
THE MIGRATION COST RAMP IS PRE-CALCULATED - COPY IT EXACTLY
================================================================================
"""
        
        return exact_costs
        
    except Exception as e:
        print(f"Warning: Could not extract exact costs from Excel: {e}")
        import traceback
        traceback.print_exc()
        return None

def calculate_migration_ramp(monthly_cost: float, timeline_months: int) -> str:
    """
    Calculate migration cost ramp based on timeline.
    Returns formatted string with exact costs for each phase.
    
    Args:
        monthly_cost: Total monthly AWS cost (from Option 1)
        timeline_months: Project timeline in months
    
    Returns:
        Formatted string with migration ramp costs
    """
    if not monthly_cost or not timeline_months:
        return ""
    
    # Ensure monthly_cost is a float
    if isinstance(monthly_cost, str):
        monthly_cost = float(monthly_cost.replace('$', '').replace(',', ''))
    
    # Define phase percentages and month ranges based on timeline
    if timeline_months <= 3:
        # 3-month timeline
        phases = [
            ("Month 1", 0.20, monthly_cost * 0.20),
            ("Month 2", 0.50, monthly_cost * 0.50),
            ("Month 3", 1.00, monthly_cost * 1.00)
        ]
        title = "3-Month Migration Cost Ramp"
    elif timeline_months <= 8:
        # 8-month timeline
        phases = [
            ("Months 1-3", 0.30, monthly_cost * 0.30),
            ("Months 4-6", 0.70, monthly_cost * 0.70),
            ("Months 7-8", 1.00, monthly_cost * 1.00)
        ]
        title = "8-Month Migration Cost Ramp"
    elif timeline_months <= 12:
        # 12-month timeline
        phases = [
            ("Months 1-4", 0.30, monthly_cost * 0.30),
            ("Months 5-8", 0.70, monthly_cost * 0.70),
            ("Months 9-12", 1.00, monthly_cost * 1.00)
        ]
        title = "12-Month Migration Cost Ramp"
    elif timeline_months <= 18:
        # 18-month timeline
        phases = [
            ("Months 1-6", 0.30, monthly_cost * 0.30),
            ("Months 7-12", 0.70, monthly_cost * 0.70),
            ("Months 13-18", 1.00, monthly_cost * 1.00)
        ]
        title = "18-Month Migration Cost Ramp"
    else:
        # 24-month timeline
        phases = [
            ("Months 1-8", 0.30, monthly_cost * 0.30),
            ("Months 9-16", 0.70, monthly_cost * 0.70),
            ("Months 17-24", 1.00, monthly_cost * 1.00)
        ]
        title = "24-Month Migration Cost Ramp"
    
    # Format the migration ramp section
    ramp_text = f"""
**{title}**:
"""
    for period, percentage, cost in phases:
        ramp_text += f"- {period}: ${cost:,.2f} ({int(percentage * 100)}% of monthly cost)\n"
    
    return ramp_text.strip()


def create_section_agent(section_prompt):
    """Create an agent for generating a specific section"""
    from agents.utils.bedrock_guardrails import create_bedrock_model_with_guardrails
    
    # Create model with guardrails if enabled
    # Use 8192 max_tokens - balanced between preventing MaxTokensReachedException and speed
    model = create_bedrock_model_with_guardrails(
        model_id=model_id_claude3_7,
        temperature=model_temperature,
        max_tokens=8192
    )
    return Agent(model=model, system_prompt=section_prompt, callback_handler=None)

# Section prompts
EXECUTIVE_SUMMARY_PROMPT = """
Generate a comprehensive Executive Summary for the AWS migration business case.

**Input**: You will receive analysis from multiple agents covering current state, costs, strategy, and migration plan.

**CRITICAL - ATX INPUT HANDLING**:
- IF ATX data is present, check for "ATX PPT PRE-COMPUTED SUMMARY" in the context
- IF found, use the Executive Summary content from ATX PowerPoint DIRECTLY
- Extract key points from the ATX Executive Summary and present them
- Use the EXACT numbers from ATX (VM counts, costs, savings percentages)
- DO NOT add information not in the ATX Executive Summary

**CRITICAL - TCO PRESENTATION RULE**:
- ONLY show cost savings metrics if AWS demonstrates lower TCO than on-premises
- If AWS costs are EQUAL or HIGHER, focus on business value and strategic benefits instead
- Do NOT show negative savings or unfavorable cost comparisons

**CRITICAL - AVOID REPETITION**:
- Keep this section HIGH-LEVEL only - detailed numbers will be in later sections
- DO NOT repeat detailed wave structures, timelines, or cost breakdowns
- Focus on KEY takeaways and strategic overview only

**Generate**:
1. Project Overview (use EXACT customer name and project details from PROJECT CONTEXT)
2. Current State Highlights - Use EXACT VM counts from analysis (e.g., "51 virtual machines" not "approximately 50")
   - FOR ATX: Use Assessment Scope numbers (Total VMs, Windows VMs, Linux VMs, Storage)
3. Recommended Approach - STRATEGIC OVERVIEW (e.g., "phased approach over 18 months" without wave details)
   - FOR ATX: Extract approach from ATX Executive Summary if mentioned
4. Key Financial Metrics - SUMMARY ONLY:
   **CRITICAL - DETERMINE WHICH PRICING TO USE**:
   
   **FOR ATX INPUT**:
   - Use Financial Overview from ATX PPT (Monthly Cost, Annual Cost, 3-Year Cost)
   - Include savings percentage if mentioned in ATX Executive Summary
   - Add note: "Pricing based on [pricing model from ATX]"
   
   **FOR RVTOOLS/IT INVENTORY INPUT**:
   - You will receive EXACT COSTS FROM EXCEL FILE in the context
   - Check if EKS ANALYSIS is available (look for "EKS RECOMMENDATION (PRE-CALCULATED)")
   
   **IF EKS ANALYSIS AVAILABLE**:
   - Check "EKS RECOMMENDATION (PRE-CALCULATED)" for recommended option
   - **IF Option 3 (Hybrid) recommended**: Use HYBRID COSTS from "HYBRID COST BREAKDOWN"
     * Show: Total Monthly, Total Annual (including backup), 3-Year Total from HYBRID section
     * Note: "Pricing based on Hybrid approach (EC2 + EKS)"
   - **IF Option 1 recommended**: Use Option 1 costs from EXACT COSTS
     * Show: Total Monthly, Total Annual (including backup), 3-Year Total from Option 1
     * Note: "Pricing based on 3-Year EC2 Instance Savings Plan"
   - **IF Option 2 recommended**: Use Option 2 costs from EXACT COSTS
     * Show: Total Monthly, Total Annual (including backup), 3-Year Total from Option 2
     * Note: "Pricing based on 3-Year Compute Savings Plan"
   
   **IF NO EKS ANALYSIS**:
   - Use Option 1 costs from EXACT COSTS section
   - For RVTools: Show monthly, annual (including backup), 3-year (no RDS fees)
   - For IT Inventory: Include RDS upfront fees and 3-year total with upfront
   
   **CRITICAL - ARR CLARIFICATION (Issue 4 Fix)**:
   - Always add "(including backup)" after Annual Cost/ARR mentions
   - Example: "Annual AWS Cost (ARR, including backup): $32,119.20"
   - This clarifies that backup costs are included in the ARR figure
   
   **CRITICAL**: Check TCO_ENABLED flag in context:
   - IF TCO_ENABLED=True AND AWS < On-Prem: Include "Break-even: Month X"
   - IF TCO_ENABLED=False OR AWS >= On-Prem: DO NOT include break-even, show business value instead
5. Expected Benefits - TOP 3-4 ONLY (detailed list will be in Benefits section)
   - FOR ATX: Extract benefits from ATX Executive Summary if mentioned
6. Critical Success Factors - TOP 3 ONLY
   - FOR ATX: Extract from ATX Executive Summary if mentioned
7. Timeline Overview - HIGH-LEVEL:
   **CRITICAL - USE ACTUAL PROJECT TIMELINE**:
   - Extract timeline from PROJECT CONTEXT (e.g., "9 months", "18 months", "24 months")
   - Use EXACT timeline in overview (e.g., "9-month phased approach" NOT "18-month")
   - DO NOT use hardcoded timelines - ALWAYS use the actual project timeline
   - Example: If project is 9 months, say "9-month phased approach" NOT "18-month"

**Format**: Markdown, 400-500 words MAX, include key metrics table
**Tone**: Executive-level, strategic, business-focused
**CRITICAL**: 
- Use ACTUAL NUMBERS from analysis - NO placeholders or approximations
- FOR ATX: Use EXACT content from ATX Executive Summary slide
- Financial metrics MUST match source data exactly
- VM counts must be exact (e.g., "51 VMs" not "approximately 50")

**❌ FORBIDDEN IN OUTPUT**:
- NO meta-commentary (e.g., "Here is a concise...", "I have generated...")
- NO word count mentions (e.g., "within 500 words", "max 300 words")
- NO instruction references (e.g., "as requested", "following the guidelines")
- NO section titles with limits (e.g., "Benefits (600 words MAX)")
- Write ONLY the actual business case content
"""

CURRENT_STATE_PROMPT = """
Generate a CONCISE Current State Analysis section.

**CONCISENESS REQUIREMENTS:**
- Maximum 600 words
- Use ONE summary table (not multiple tables)
- Reference Excel files for detailed VM-level data
- Focus on high-level insights, not data dumps

**Input**: Analysis from current_state_analysis, IT inventory, RVTools, ATX, and MRA agents.

**CRITICAL - MRA INTEGRATION**:
- IF MRA analysis available, include organizational readiness summary
- Reference MRA findings in "Organizational Readiness" subsection
- Use MRA scores/ratings if available (e.g., "Cloud readiness: Medium (MRA score: 3/5)")
- Keep MRA content concise (2-3 sentences max)

**CRITICAL - ATX INPUT HANDLING**:
- IF ATX data is present, use Assessment Scope numbers only
- DO NOT add vCPU or RAM if not in ATX Assessment Scope
- DO NOT hallucinate workload types or technical details

**Generate** (CONCISE):
1. IT Infrastructure Overview (1 table, 5-7 rows max):
   - Total VMs/Servers
   - OS distribution (Linux/Windows) - **IMPORTANT for EKS analysis**
   - vCPU, RAM, Storage (if available)
   - Databases (if IT Inventory)
   - **Reference**: "See Excel files for detailed VM-level inventory and cost analysis"

2. Key Challenges (3-5 bullet points only):
   - From ACTUAL analysis findings
   - Project-specific challenges only

3. Technical Debt (2-3 bullet points):
   - From ACTUAL assessment data
   - Omit if not mentioned in source

4. Organizational Readiness (1 paragraph):
   - From MRA findings (if available)
   - Include MRA readiness scores if available
   - Omit if no MRA

**Format**: Markdown, 600 words MAX, ONE summary table
**CRITICAL**: 
- Use ACTUAL numbers - NO placeholders
- Reference Excel files for VM-level details and cost breakdowns
- IF MRA available, include organizational readiness summary

**❌ FORBIDDEN IN OUTPUT**:
- NO meta-commentary (e.g., "Here is...", "I have created...")
- NO word count mentions (e.g., "within 600 words")
- NO instruction references
- Write ONLY the actual section content
"""

MIGRATION_STRATEGY_PROMPT = """
🚨🚨🚨 STOP! READ THIS FIRST! 🚨🚨🚨

LOOK FOR "PRE-COMPUTED RVTOOLS SUMMARY" OR "EXACT COSTS FROM EXCEL FILE" IN YOUR CONTEXT.

DOES IT SAY "Total VMs" OR "Total Servers and Total Databases"?

IF "Total VMs" → THIS IS RVTOOLS = EC2 ONLY, NO DATABASES EXIST
  ❌ FORBIDDEN: RDS, Aurora, DynamoDB, database migration, Oracle, SQL Server, MySQL
  ❌ DO NOT write about migrating databases
  ✅ ALLOWED: Lambda, ECS, EKS, Fargate, EC2, Auto Scaling, S3, CloudFront

IF "Total Servers" AND "Total Databases" → THIS IS IT INVENTORY = EC2 + DATABASES
  ✅ ALLOWED: Include database migration strategies

🚨 IF YOU WRITE "RDS" WHEN INPUT SAYS "Total VMs", YOU FAILED 🚨

================================================================================

Generate a CONCISE Migration Strategy section.

**CONCISENESS REQUIREMENTS:**
- Maximum 500 words
- Use tables for 7Rs distribution (not detailed explanations)
- Focus on strategic approach (not detailed 7Rs definitions)
- DO NOT include detailed wave planning (keep high-level only)

**Input**: Analysis from agent_migration_strategy covering 7Rs recommendations.
  
- IF you see "Total Servers" AND "Total Databases" (IT Inventory): Include both EC2 and database strategies

**CRITICAL - TIMELINE REQUIREMENT**: 
- Check PROJECT CONTEXT for migration timeline (e.g., "18 months", "24 months")
- ALL phases and waves MUST fit within this EXACT timeline
- DO NOT exceed the specified duration
- Example for 18 months: Wave 1 (Months 1-6) + Wave 2 (Months 7-12) + Wave 3 (Months 13-18) = 18 months
- Example for 24 months: Wave 1 (Months 1-8) + Wave 2 (Months 9-16) + Wave 3 (Months 17-24) = 24 months

**CRITICAL - DEPRECATED SERVICES**: Ensure all AWS service recommendations are current and NOT deprecated. Reference: https://aws.amazon.com/products/lifecycle/

**CRITICAL - EKS INTEGRATION**:
- IF EKS analysis available in context, mention container strategy
- Example: "Replatform: [X] Linux VMs to EKS containers"
- Keep EKS mention brief (1 sentence in 7Rs or Quick Wins)
- Detailed EKS analysis is in Excel, not business case

**Generate** (concise - 500 words MAX):
1. Recommended Approach (1 paragraph):
   - Mention EXACT timeline from PROJECT CONTEXT
   - IF EKS available: Mention hybrid approach (EC2 + EKS)
   - High-level strategy overview

2. AWS 7Rs Framework Overview (1-2 sentences):
   - Brief explanation: "The AWS 7Rs framework provides a structured approach to cloud migration, categorizing workloads by transformation level—from simple lift-and-shift (Rehost) to complete modernization (Refactor/Re-architect)."
   - Clarify: "The 7Rs distribution below is based on industry-standard patterns for similar infrastructure profiles and does NOT directly impact pricing calculations, which are based on actual infrastructure specifications. Application Assessment is required to determine and map each application to 7R migration strategy"

3. 7Rs Distribution (table format):
   - Use actual numbers or percentages (NO "TBD" values)
   - If exact numbers unavailable, use reasonable estimates
   - IF EKS available: Include "Replatform to EKS" row
   - Example table:
   
   | Strategy | Count/% | Description |
   |----------|---------|-------------|
   | Rehost | 40% | Lift-and-shift to EC2 |
   | Replatform | 25% | Minor optimizations, EKS containers |
   | Refactor | 15% | Modernize to serverless |
   | ... | ... | ... |

4. Wave Planning (2-3 sentences):
   - High-level wave structure
   - Waves MUST fit within project timeline
   - Keep strategic overview only (no detailed wave breakdown)

5. Quick Wins (3-5 bullets):
   - FOR RVTOOLS (Total VMs): EC2/compute quick wins ONLY (Lambda, Fargate, ECS, EKS, Auto Scaling)
   - ❌ DO NOT mention: RDS, Aurora, DynamoDB, database migration, database consolidation
   - FOR IT INVENTORY (Total Servers + Databases): Include both compute and database quick wins
   - IF EKS available: Include "Containerize stateless Linux apps" as quick win

**❌ DO NOT INCLUDE**:
- Cost Analysis (separate section)
- Cost estimates or ranges
- TCO comparisons
- Financial metrics
- Detailed 7Rs explanations (everyone knows what Rehost means)

**Format**: Markdown, 500 words MAX, tables and bullets
**Tone**: Strategic, practical, actionable
**CRITICAL**: 
- NO detailed 7Rs definitions
- RESPECT project timeline
- Integrate EKS mention (not separate subsection)

**❌ FORBIDDEN IN OUTPUT**:
- NO meta-commentary (e.g., "Here is a concise...", "I maintained...")
- NO word count mentions (e.g., "within 500 words", "max 3 bullets")
- NO instruction references
- NO section titles with limits
- Write ONLY the actual section content
"""

def get_cost_analysis_prompt():
    """Generate cost analysis prompt based on TCO config"""
    tco_enabled = TCO_COMPARISON_CONFIG.get('enable_tco_comparison', False)
    
    if tco_enabled:
        return """
🚨🚨🚨 MANDATORY FIRST STEP - READ THIS BEFORE ANYTHING ELSE 🚨🚨🚨

LOOK AT THE "EXACT COSTS FROM EXCEL FILE" SECTION IN YOUR CONTEXT.

QUESTION: Does it say "Total VMs" OR does it say "Total Servers" and "Total Databases"?

IF "Total VMs" → RVTools input = EC2 ONLY, NO RDS, NO DATABASES
  ❌ FORBIDDEN: Never write "RDS", "database" (in cost context), "upfront", "+ RDS"
  ✅ ALLOWED: Only write "EC2", "EBS", "Storage", "Compute"

IF "Total Servers" AND "Total Databases" → IT Inventory = EC2 + RDS
  ✅ ALLOWED: You can mention RDS and databases

🚨 IF YOU WRITE "RDS" WHEN INPUT SAYS "Total VMs", YOU COMPLETELY FAILED 🚨

================================================================================

Generate a concise Cost Analysis and TCO section.

**Input**: Analysis from agent_aws_cost_arr covering AWS costs and TCO.

**CRITICAL - ATX INPUT HANDLING**:
- IF ATX data is present, check for "ATX PPT PRE-COMPUTED SUMMARY" and "Financial Overview" content
- IF found, use the Financial Overview content from ATX PowerPoint AS-IS
- Present the ATX financial data exactly as provided in the PowerPoint
- Include any cost breakdowns, savings percentages, or comparisons mentioned in ATX
- DO NOT add TCO calculations unless explicitly in the ATX Financial Overview
- DO NOT modify or reformat the ATX financial content

**CRITICAL - DEPRECATED SERVICES**: Do NOT include any deprecated or end-of-life AWS services in cost analysis. Only include current, actively supported services. Reference: https://aws.amazon.com/products/lifecycle/

**CRITICAL - TCO VALIDATION RULE**:
- ONLY show on-premises TCO comparison if AWS demonstrates cost savings (AWS < On-Prem)
- If AWS costs are EQUAL or HIGHER than on-premises, SKIP the TCO comparison table
- Instead, focus on business value: agility, innovation, scalability, reduced technical debt
- Emphasize strategic advantages and operational benefits over pure cost comparison

**CRITICAL - COST NUMBERS MUST BE EXACT**:
Before generating this section, the system will extract exact costs from the Excel file and provide them to you.
You MUST use these exact numbers - DO NOT modify, round, or estimate them.

**Generate** (very concise):

**🚨 FIRST: CHECK INPUT TYPE 🚨**
Look at the "EXACT COSTS FROM EXCEL FILE" section you received:
- Does it mention "ATX PPT PRE-COMPUTED SUMMARY"? → ATX INPUT (use ATX section below)
- Does it say "Total Servers" and "Total Databases"? → IT INVENTORY INPUT (use IT Inventory section below)
- Does it say "Total VMs" ONLY? → RVTOOLS INPUT (use RVTools section below)

**FOR ATX INPUT ONLY** (if "ATX PPT PRE-COMPUTED SUMMARY" is present):
1. Financial Overview (from ATX PowerPoint) - Present AS-IS:
   - Include the complete Financial Overview content from ATX
   - Use EXACT numbers from ATX (monthly, annual, 3-year costs)
   - Include any savings percentages or comparisons mentioned
   - Add any assumptions or notes from ATX Financial Overview slide
   - DO NOT add TCO comparison unless in ATX data
   - DO NOT modify the ATX financial content

**FOR RVTOOLS/IT INVENTORY INPUT** (if no ATX data):

**🚨 CRITICAL FIRST STEP - DETECT INPUT TYPE 🚨**:
BEFORE writing anything, CHECK the exact costs section you received:
- Does it say "Total Servers" and "Total Databases"? → IT Inventory (has EC2 + RDS)
- Does it say "Total VMs" ONLY? → RVTools (EC2 ONLY, NO RDS EXISTS)

IF RVTOOLS (Total VMs only):
- ❌ DO NOT mention RDS anywhere
- ❌ DO NOT mention databases in cost context
- ❌ DO NOT mention upfront fees
- ❌ DO NOT add "+ RDS" to option titles
- ❌ DO NOT make up RDS cost breakdowns
- ✅ ONLY mention EC2, EBS, Storage, Compute

1. AWS Cost Summary:
   
   **🚨 CRITICAL - DO NOT GENERATE OPTION DETAILS 🚨**:
   A detailed pricing comparison TABLE will be automatically inserted at the beginning of this section.
   The table contains ALL cost details for Option 1, Option 2, and Option 3.
   
   **DO NOT GENERATE**:
   - ❌ Option 1 cost details (EC2 Monthly, RDS Monthly, Total Monthly, etc.)
   - ❌ Option 2 cost details
   - ❌ Option 3 cost details
   - ❌ Cost difference calculations
   - ❌ Any text-based cost listings
   
   **YOUR TASK - ONLY GENERATE THESE 3 SECTIONS**:
   
   a) **Notes** section (after the table):
      - Explain what each option represents
      - For IT Inventory: Mention RDS pricing models (Partial Upfront vs No Upfront)
      - For RVTools: Only mention EC2 pricing models
      - Note that all costs include AWS Backup with intelligent tiering
      - Add Excel file reference (see below)
   
   b) **Recommended Option** section:
      - Look for "EKS RECOMMENDATION (PRE-CALCULATED)" in context
      - Copy the recommendation exactly as provided
      - Format: "**Recommended: [Option Name]**"
      - Include the rationale from EKS RECOMMENDATION
   
   c) **Cost Breakdown (Recommended Option)** section:
      - Show detailed breakdown ONLY for the recommended option
      - Use exact costs from the context provided
   
   **Cost Breakdown (Recommended Option)**:
   - Show detailed breakdown ONLY for the recommended option
   - EC2 Components (if applicable):
     - Compute (EC2 Instances): $[from exact costs]/month
     - Storage (EBS gp3): $[from exact costs]/month
   - EKS Components (if Option 3 recommended):
     - Control Plane: $[from EKS context]
     - Worker Nodes: $[from EKS context]
     - Storage: $[from EKS context]
     - Networking: $[from EKS context]
   - RDS Components (if IT Inventory):
     - RDS Instances: $[from exact costs]/month
     - RDS Storage: $[from exact costs]/month
   - **Backup (AWS Backup with intelligent tiering)**: [from exact costs - backup_monthly_cost]
     * Production VMs: [from backup_costs.production.monthly_cost]
     * Pre-Production VMs: [from backup_costs.pre_production.monthly_cost]
     * Storage Tiering Savings: [from backup_costs.savings_vs_all_warm] ([from backup_costs.savings_percentage]%)
     * Note: "Includes daily (7d), weekly (2w), monthly (1yr) backups with S3 Glacier tiering"
   
   d) **Excel File Reference** (add at end of AWS Cost Summary section):
      
      **📊 Detailed Analysis Available:**
      
      **FOR IT INVENTORY INPUT**:
      - **IT Inventory Pricing Excel** (`it_inventory_aws_pricing_*.xlsx`): Server-by-server and database-by-database cost breakdown, instance type mappings, storage costs, RDS configurations, and backup costs
      
      **FOR RVTOOLS INPUT**:
      - **RVTools Pricing Excel** (`rvtools_aws_pricing_*.xlsx`): VM-by-VM cost breakdown with instance type mappings, storage costs, and backup costs
   
2. **Assumptions** (brief section showing key assumptions used in cost modeling):
   - Peak CPU Utilization: 25%
   - Peak Memory Utilization: 60%
   - Storage Utilization (if missing data): 50%
   - Default Provisioned Storage (if missing data): 500 GiB
   
3. **IF AWS < On-Prem**: Include On-Premises TCO Calculation Methodology and comparison table (use Option 1/EC2 Instance Savings Plan pricing)
4. **IF AWS >= On-Prem**: Skip TCO comparison, focus on business value and strategic benefits
5. Migration Cost Ramp (table showing gradual AWS cost increase as workloads migrate - use project timeline from PROJECT CONTEXT)
   - **🚨 CRITICAL: USE RECOMMENDED OPTION PRICING 🚨**
   - IF Option 1 or 2 recommended: Use pre-calculated migration ramp from exact costs
   - IF Option 3 (Hybrid) recommended: Recalculate using HYBRID TOTAL MONTHLY × percentages
   - **IMPORTANT**: Only use Option 3 pricing if it was actually recommended based on the logic above
   - Keep same timeline and phase structure as pre-calculated ramp
6. Cost Optimization opportunities (bullet points)
7. **IF AWS < On-Prem**: Break-Even Analysis (1 paragraph)
8. **IF AWS >= On-Prem**: Business Value Justification (agility, innovation, time-to-market, reduced operational complexity)

**CRITICAL REQUIREMENTS FOR DETERMINISTIC CALCULATIONS**:
- Show pricing options based on EKS availability:
  * IF EKS DISABLED: Show 2 options (Option 1: EC2 Instance SP, Option 2: Compute SP)
  * IF EKS ENABLED: Show 3 options (Option 1: EC2 Instance SP, Option 2: Compute SP, Option 3: Hybrid EC2+EKS)
- For IT Inventory with RDS:
  * Option 1 (Recommended): "EC2 Instance SP (3yr) + RDS Partial Upfront (3yr)"
  * Option 2: "Compute SP (3yr) + RDS No Upfront (1yr × 3)"
  * Option 3 (if EKS): "Hybrid (EC2 + EKS) with RDS"
- Use the EXACT cost calculations from the cost analysis provided - DO NOT recalculate
- Ensure ALL cost figures are CONSISTENT throughout the section (don't show $6.2M in one place and $516K in another)
- If cost analysis shows "Year 1 AWS: $X", use that EXACT figure - don't round or estimate
- On-premises costs should be HIGHER than AWS costs
- Explain RDS pricing difference: 3-year Partial Upfront (lower monthly, requires upfront payment) vs 1-year No Upfront (higher monthly, no upfront payment)
- **RECOMMENDATION LOGIC**:
  * Calculate cost difference between all options
  * Apply 4-tier decision tree (see Cost Analysis section above)
  * IF Option 3 is cheapest OR costs 0-20% more: Recommend Option 3 (strategic value)
  * IF Option 3 costs 20-50% more: Recommend cheapest EC2 option (cost-sensitive)
  * IF Option 3 costs 50%+ more: Recommend cheapest EC2 option only (EKS not viable)
  * Clearly state rationale for recommendation
- **MIGRATION COST RAMP**: Use the ACTUAL project timeline from PROJECT CONTEXT (e.g., 3 months, 12 months, 18 months, 24 months)
  * For 3 months: Month 1 (20%), Month 2 (50%), Month 3 (100%)
  * For 12 months: Months 1-4 (30%), Months 5-8 (70%), Months 9-12 (100%)
  * For 18 months: Months 1-6 (30%), Months 7-12 (70%), Months 13-18 (100%)
  * For 24 months: Months 1-8 (30%), Months 9-16 (70%), Months 17-24 (100%)
  * Title should match timeline: "3-Month Migration Cost Ramp" or "12-Month Migration Cost Ramp" or "18-Month Migration Cost Ramp"
  * **CALCULATION**: Monthly cost × percentage (e.g., $2,676.60 × 0.30 = $803.00, NOT $2,676.60 × 4 months)
- Use actual VM counts and specs from the analysis
- Include cost breakdown by service (Compute, Storage, Database, Networking)
- Show calculation basis with actual numbers from the analysis

**CRITICAL - OS DISTRIBUTION**: Use Windows/Linux VM counts from PRE-COMPUTED RVTOOLS SUMMARY exactly as provided.

**Format**: Markdown, 500-600 words MAX, proper tables
**Tone**: Financial, analytical, data-driven
"""
    else:
        return """
Generate a concise Cost Analysis section (TCO COMPARISON DISABLED).

**Input**: Analysis from agent_aws_cost_arr covering AWS costs.

**CRITICAL - INPUT TYPE CHECK**:
- "Total VMs" = RVTools = EC2 ONLY (no RDS/databases)
- "Total Servers" + "Total Databases" = IT Inventory = EC2 + RDS

**ATX INPUT**: If ATX data present, use Financial Overview from ATX PowerPoint AS-IS.
- DO NOT modify or reformat the ATX financial content

**CRITICAL - TCO DISABLED**: 
- DO NOT include on-premises cost calculations
- DO NOT show TCO comparison tables
- DO NOT show break-even analysis
- DO NOT mention cost savings vs on-premises
- DO NOT calculate or reference on-premises infrastructure costs
- Focus ONLY on AWS costs and business value

**CRITICAL - DEPRECATED SERVICES**: Do NOT include any deprecated or end-of-life AWS services in cost analysis. Only include current, actively supported services.

**Generate** (very concise):

**🚨 FIRST: CHECK INPUT TYPE 🚨**
Look at the "EXACT COSTS FROM EXCEL FILE" section you received:
- Does it mention "ATX PPT PRE-COMPUTED SUMMARY"? → ATX INPUT (use ATX section below)
- Does it say "Total Servers" and "Total Databases"? → IT INVENTORY INPUT (use IT Inventory section below)
- Does it say "Total VMs" ONLY? → RVTOOLS INPUT (use RVTools section below)

**FOR ATX INPUT ONLY** (if "ATX PPT PRE-COMPUTED SUMMARY" is present):
1. Financial Overview (from ATX PowerPoint) - Present AS-IS:
   - Include the complete Financial Overview content from ATX
   - Use EXACT numbers from ATX (monthly, annual, 3-year costs)
   - Include any savings percentages or comparisons mentioned
   - Add any assumptions or notes from ATX Financial Overview slide
   - DO NOT add TCO comparison
   - DO NOT modify the ATX financial content

**FOR RVTOOLS/IT INVENTORY INPUT** (if no ATX data):

**🚨 CRITICAL FIRST STEP - DETECT INPUT TYPE 🚨**:
BEFORE writing anything, CHECK the exact costs section you received:
- Does it say "Total Servers" and "Total Databases"? → IT Inventory (has EC2 + RDS)
- Does it say "Total VMs" ONLY? → RVTools (EC2 ONLY, NO RDS EXISTS)

IF RVTOOLS (Total VMs only):
- ❌ DO NOT mention RDS anywhere
- ❌ DO NOT mention databases in cost context
- ❌ DO NOT mention upfront fees
- ❌ DO NOT add "+ RDS" to option titles
- ❌ DO NOT make up RDS cost breakdowns
- ✅ ONLY mention EC2, EBS, Storage, Compute

1. AWS Cost Summary - Show BOTH pricing options:
   
   **FOR IT INVENTORY (EC2 + RDS) - Use this format if "Total Databases" is in exact costs**:
   
   **Option 1: EC2 Instance SP (3yr) + RDS Partial Upfront (3yr) - RECOMMENDED**
   - Total Monthly AWS Cost
   - Total Annual AWS Cost (ARR, including backup)
   - 3-Year Pricing: Monthly × 36 months
   - RDS Upfront Fees (One-time): Show if applicable
   - 3-Year Total Cost (incl. upfront): 3-Year Pricing + RDS Upfront Fees
   - Note: "Based on 3-Year EC2 Instance Savings Plan for EC2 and 3-Year Partial Upfront RI for RDS"
   
   **Option 2: Compute SP (3yr) + RDS No Upfront (1yr × 3)**
   - Total Monthly AWS Cost
   - Total Annual AWS Cost (ARR, including backup)
   - 3-Year Pricing: Monthly × 36 months
   - RDS Upfront Fees (One-time): Show if applicable (typically $0 for No Upfront)
   - 3-Year Total Cost (incl. upfront): 3-Year Pricing + RDS Upfront Fees
   - Note: "Based on 3-Year Compute Savings Plan for EC2 and 1-Year No Upfront RI (renewed 3 times) for RDS"
   - Cost difference vs Option 1: Show savings amount and breakdown (EC2 + RDS)
   
   **FOR RVTOOLS (EC2 ONLY) - Use this EXACT format if "Total VMs" is in exact costs**:
   
   🚨 REMINDER: If you see "Total VMs", DO NOT write "RDS", "database", "upfront", "+ RDS" 🚨
   
   **Option 1: 3-Year EC2 Instance Savings Plan - RECOMMENDED**
   - Total Monthly AWS Cost: [copy exact value]
   - Total Annual AWS Cost (ARR, including backup): [copy exact value]
   - 3-Year Pricing: [copy exact value]
   - Note: Based on 3-Year EC2 Instance Savings Plan
   
   **Option 2: 3-Year Compute Savings Plan - All VMs on EC2** 
   - Total Monthly AWS Cost: [copy exact value]
   - Total Annual AWS Cost (ARR, including backup): [copy exact value]
   - 3-Year Pricing: [copy exact value]
   - Note: Based on 3-Year Compute Savings Plan
   - Cost difference vs Option 1: 
     - Monthly Savings: [copy exact value]
     - Annual Savings: [copy exact value]
     - 3-Year Savings: [copy exact value]
     - Savings Percentage: [copy exact value]
   
   **🚨 IF EKS ANALYSIS AVAILABLE: ADD OPTION 3 🚨**
   
   **Option 3: Hybrid (EC2 + EKS)**
   - EC2 Monthly ([X] VMs): $[from EKS context "HYBRID COST BREAKDOWN" - ec2_monthly]
   - EKS Monthly ([Y] VMs): $[from EKS context "HYBRID COST BREAKDOWN" - eks_monthly]
   - **Total Monthly: $[from EKS context "HYBRID TOTAL MONTHLY"]**
   - **Total Annual (including backup): $[from EKS context "HYBRID TOTAL ANNUAL"]**
   - **3-Year Total: $[from EKS context "HYBRID TOTAL 3-YEAR"]**
   - Note: "Combines EC2 for [X] VMs with EKS containerization for [Y] Linux VMs"
   
   **Cost Comparison (if EKS available):**
   - Option 1 vs Option 3: $[calculate: Option 3 3yr - Option 1 3yr] ([calculate %])
   - Option 2 vs Option 3: $[calculate: Option 3 3yr - Option 2 3yr] ([calculate %])
   
   **🚨 PRE-CALCULATED RECOMMENDATION (DO NOT CHANGE) 🚨**
   
   The recommendation has been calculated using deterministic Python logic based on the 4-tier decision tree.
   You will find "EKS RECOMMENDATION (PRE-CALCULATED)" in the context with:
   - Recommended Option: [from recommendation data]
   - Tier: [from recommendation data]
   - Cost Difference: [from recommendation data]
   - Rationale Text: [from recommendation data]
   
   **YOUR TASK**: 
   - Copy the recommended option EXACTLY as provided
   - Use the rationale text provided (you may expand it slightly for clarity)
   - DO NOT change the recommendation based on cost alone
   - DO NOT apply your own logic - the decision is already made
   
   **Format**:
   - **Recommended: [Copy from recommendation data]**
   - **Rationale:** [Copy and optionally expand the rationale text provided]
   
   **Cost Breakdown** (use Option 1 pricing, or Option 3 if EKS available):
   - Breakdown by service (Compute, Storage) - ❌ NO "Database" or "RDS"
   - **IF EKS ANALYSIS AVAILABLE**: Show hybrid breakdown:
     * EC2 (Compute + Storage): $[from hybrid costs]
     * EKS (Control Plane + Workers + Storage + Networking): $[from hybrid costs]
     * Total: $[from hybrid costs]
   - Breakdown by instance type (ONLY if provided in cost analysis - DO NOT make up instance counts)
   - Cost per VM average
   
   **CRITICAL - INSTANCE DISTRIBUTION**:
   - ONLY include instance types explicitly mentioned in the cost analysis
   - Instance counts MUST sum to the total VM count (e.g., if 51 VMs total, all instance counts must sum to 51)
   - DO NOT list made-up instance types or counts
   - If instance distribution not provided, SKIP this subsection
   
   **CRITICAL - OS DISTRIBUTION**:
   - SEARCH for "PRE-COMPUTED RVTOOLS SUMMARY" in the context
   - Use ONLY the Windows VMs and Linux VMs counts from that summary
   - Windows + Linux counts MUST equal total migrating VMs from the summary
   - Example: If summary shows "Windows VMs: 781" and "Linux VMs: 1246", use EXACTLY those numbers
   - These counts are consistent with pricing calculator (Other VMs are treated as Linux)
2. **Assumptions** (brief section showing key assumptions used in cost modeling):
   - Peak CPU Utilization: 25%
   - Peak Memory Utilization: 60%
   - Storage Utilization (if missing data): 50%
   - Default Provisioned Storage (if missing data): 500 GiB

3. Migration Cost Ramp
   - **🚨 CRITICAL: USE RECOMMENDED OPTION PRICING 🚨**
   - The "EXACT COSTS FROM EXCEL FILE" section includes a pre-calculated "MIGRATION COST RAMP" based on Option 1
   - **IF Option 1 or Option 2 is recommended**: Copy the pre-calculated migration ramp exactly as provided
   - **IF Option 3 (Hybrid) is recommended**: Recalculate using Option 3 monthly cost:
     * Use the same timeline and percentages from the pre-calculated ramp
     * Replace the monthly cost with Option 3 HYBRID TOTAL MONTHLY
     * Calculate: HYBRID TOTAL MONTHLY × percentage for each phase
     * Example: If HYBRID TOTAL MONTHLY = $2,676.60 and phase is 30%, show $803.00
   - Format: Keep the same title and structure as pre-calculated ramp
   - DO NOT show on-premises cost reduction
4. Cost Optimization Opportunities (bullet points, 5-7 items)
   - Compute Savings Plans and EC2 Instance Savings Plans (already included in pricing)
   - Right-sizing recommendations
   - Storage optimization
   - Spot instances for suitable workloads
5. Business Value Justification (focus on strategic benefits, NOT cost savings)
   - Agility and faster time-to-market
   - Innovation enablement (AI/ML, analytics, modern services)
   - Reduced technical debt and operational complexity
   - Global scalability and reliability
   - Security and compliance improvements

**CRITICAL REQUIREMENTS**:
- Use the EXACT cost calculations from the cost analysis provided - DO NOT recalculate
- Ensure ALL cost figures are CONSISTENT throughout the section
- Use actual VM counts and specs from the analysis
- Include cost breakdown by service (Compute, Storage, Database, Networking)
- Show calculation basis: "Based on X VMs with average cost of $Y per VM"
- DO NOT mention on-premises costs anywhere

**Format**: Markdown, 400-500 words MAX, proper tables
**Tone**: Financial, analytical, business-value focused
**CRITICAL**: NO on-premises costs. Ensure cost consistency. NO meta-commentary.
"""

COST_ANALYSIS_PROMPT = get_cost_analysis_prompt()

MIGRATION_ROADMAP_PROMPT = """
Generate a CONCISE Migration Roadmap section.

**CONCISENESS REQUIREMENTS:**
- Maximum 600 words
- Phase-level only (NOT week-by-week breakdown)
- Use ONE summary table for phases
- Use actual wave plan if available, otherwise provide high-level phases
- Focus on high-level timeline and milestones

**Input**: Analysis from agent_migration_plan covering MAP methodology.

**CRITICAL - WAVE PLANNING LOGIC**:
1. **CHECK for wave plan in migration_plan output**:
   - Look for "Migration Wave Plan" section or wave table
   - IF wave plan exists: Use it directly (dependency-based waves available)
   - IF no wave plan: Provide high-level phases and note dependency mapping needed

2. **IF WAVE PLAN AVAILABLE** (from IT Infrastructure Inventory dependencies):
   - Use the actual wave assignments provided
   - Include wave table showing applications, servers, dependencies
   - Reference that waves are based on dependency analysis
   - Example: "Wave 1: 5 independent applications (8 servers)"

3. **IF NO WAVE PLAN** (no dependency data):
   - Provide HIGH-LEVEL phase structure
   - DO NOT create fake wave groupings
   - Be transparent: "Detailed wave planning requires application dependency mapping"
   - Recommend AWS Application Discovery Service

**CRITICAL - TIMELINE**: Check PROJECT CONTEXT for timeline (e.g., "3 months", "18 months"). ALL phases MUST fit within this EXACT timeline.

**CRITICAL - EKS INTEGRATION**: 
- IF EKS analysis available in context, integrate container migration into phases
- Mention EKS migration as part of compute migration (1 paragraph max)
- Example: "Phase 2 includes containerization of [X] Linux VMs to EKS"
- DO NOT create separate EKS phase - integrate into existing phases

**Generate** (concise - 600 words MAX):

**IF WAVE PLAN AVAILABLE**:
1. Migration Wave Plan (use actual wave table from migration_plan):
   - Include wave table with applications, servers, dependencies
   - Show timeline per wave
   - Reference dependency analysis source
   - IF EKS available: Note which waves include container migration

2. Key Milestones (5-7 bullets with relative timing):
   - Major deliverables per wave
   - IF EKS available: Include "EKS cluster deployment" milestone

3. Success Criteria (5 bullets max):
   - Measurable outcomes per wave

**IF NO WAVE PLAN**:
1. Phased Approach (table format, 3-4 phases max):
   - Phase name, duration, key activities
   - IF EKS available: Mention container migration in relevant phase
   - Use relative timeframes (Month 1-3, Quarter 1, etc.)
   - **DO NOT assign specific VMs to waves** (no dependency data available)

2. Key Milestones (5-7 bullets with relative timing):
   - Major deliverables and checkpoints
   - IF EKS available: Include "EKS cluster deployment" milestone
   - Use relative timing (Month 1, Week 2, etc.)

3. Success Criteria (5 bullets max):
   - Measurable outcomes for each phase
   - IF EKS available: Include containerization success metrics

4. Dependencies and Prerequisites (3-5 bullets):
   - Critical path items
   - **MUST include**: "Application dependency mapping required for detailed wave planning"
   - IF EKS available: Mention container readiness assessment
   - Recommend AWS Application Discovery Service or Migration Hub

**WAVE PLANNING NOTE**:
- **IF wave plan available**: Add note: "Wave plan generated from IT Infrastructure Inventory dependency analysis."
- **IF no wave plan**: Add note: "Detailed migration wave assignments require application dependency mapping, which is not available in the current assessment data. We recommend using AWS Application Discovery Service or AWS Migration Hub to map dependencies before creating detailed wave plans."

**Example table format (when NO wave plan)**:
| Phase | Duration | Key Activities |
|-------|----------|----------------|
| Assess & Plan | Months 1-2 | Discovery, dependency mapping, MRA, detailed wave planning |
| Mobilize | Months 3-4 | Landing zone, tools setup, pilot selection |
| Migrate | Months 5-12 | Phased migration execution (waves TBD after dependency mapping) |
| Optimize | Months 13-18 | Cost optimization, modernization |

**Format**: Markdown, 600 words MAX, use relative timeframes (Month 1-3, Quarter 1)
**Tone**: Project management focused, clear milestones, transparent about limitations
**CRITICAL**: 
- CHECK for wave plan in migration_plan output FIRST
- Use actual wave plan if available
- NO fake wave assignments without dependency data
- BE TRANSPARENT about need for dependency mapping (if no wave plan)
- Integrate EKS into existing phases (not separate)

**❌ FORBIDDEN IN OUTPUT**:
- NO meta-commentary (e.g., "The roadmap provides...", "I have created...")
- NO word count mentions (e.g., "within 600 words", "concise format")
- NO instruction references
- NO fake wave groupings without dependency data
- Write ONLY the actual roadmap content
"""

BENEFITS_RISKS_PROMPT = """
Generate a CONCISE Benefits and Risks section.

**CONCISENESS REQUIREMENTS:**
- Benefits: Maximum 600 words
- Risks: Maximum 400 words
- Use bullet points (not paragraphs)
- Focus on project-specific items (not generic cloud benefits/risks)
- Reference Excel for detailed analysis

**Input**: All previous agent analyses including MRA (Migration Readiness Assessment).

**CRITICAL - NO SPECIFIC COST NUMBERS IN BENEFITS**:
- DO NOT include specific dollar amounts, percentages, or cost figures in the Benefits section
- Detailed cost information is already provided in the Cost Analysis section
- Focus on QUALITATIVE benefits only (e.g., "Predictable monthly costs" not "$X to $Y")
- This prevents LLM hallucination of incorrect numbers
- Example GOOD: "Predictable monthly costs with gradual migration ramp-up"
- Example BAD: "Predictable monthly costs with gradual ramp-up ($894.03 to $2,980.10)"

**CRITICAL - MRA INTEGRATION**:
- IF MRA analysis available, incorporate MRA findings into risks and mitigation strategies
- Use MRA readiness scores to identify organizational and technical risks
- Reference MRA gaps in risk mitigation recommendations
- Example: "Skills gap in cloud operations (MRA score: 2/5) → Mitigation: AWS training program"

**IMPORTANT**: Emphasize both financial AND strategic/operational benefits. Even if cost savings are minimal, highlight:
- Agility and faster time-to-market
- Innovation enablement (AI/ML, analytics, modern services)
- Reduced technical debt and operational complexity
- Global scalability and reliability
- Security and compliance improvements

**CRITICAL - EKS INTEGRATION**:
- IF EKS analysis available, include EKS-specific benefits and risks
- EKS Benefits: Consolidation savings, DevOps velocity, auto-scaling, Kubernetes ecosystem
- EKS Risks: Container learning curve, application refactoring needs, Kubernetes complexity
- Keep EKS content integrated (not separate subsection)

**Generate** (concise):

### Benefits and Business Value (600 words MAX)

1. **Financial Benefits** (3-5 bullets):
   - **CRITICAL**: NO specific dollar amounts or percentages
   - Focus on qualitative financial benefits
   - Cost predictability and optimization
   - IF EKS available: Consolidation savings from containerization (qualitative only)
   - Reduced capital expenditure
   - Elimination of hardware refresh cycles
   - **Example GOOD**: "Predictable monthly costs with gradual migration ramp-up"
   - **Example BAD**: "Predictable monthly costs with gradual ramp-up ($894.03 to $2,980.10)"

2. **Operational Benefits** (4-6 bullets):
   - Reduced operational complexity
   - IF EKS available: DevOps automation and CI/CD improvements
   - Improved reliability and uptime
   - Faster provisioning and deployment
   - Automated patching and updates

3. **Strategic Benefits** (4-6 bullets):
   - Agility and faster time-to-market
   - Innovation enablement (AI/ML, analytics, containers)
   - IF EKS available: Cloud-native architecture and microservices readiness
   - Global scalability
   - Competitive advantage

4. **Security and Compliance** (2-4 bullets):
   - Enhanced security posture
   - Compliance certifications
   - Automated security controls

### Risks and Mitigation (400 words MAX)

**CRITICAL**: Focus on PROJECT-SPECIFIC risks only (not generic cloud risks everyone knows)
**CRITICAL**: IF MRA available, use MRA findings to identify and prioritize risks

1. **Technical Risks** (3-5 bullets with mitigation):
   - Project-specific technical challenges (reference MRA technical readiness if available)
   - IF EKS available: Container migration complexity → Mitigation: Phased approach, training
   - Application compatibility issues → Mitigation: Thorough testing
   - Data migration risks → Mitigation: Pilot waves, rollback plans

2. **Organizational Risks** (2-4 bullets with mitigation):
   - Skills gaps (reference MRA skills assessment if available) → Mitigation: Training programs, AWS support
   - IF EKS available: Kubernetes learning curve → Mitigation: Managed EKS, AWS training
   - Change management (reference MRA change readiness if available) → Mitigation: Communication plan, stakeholder engagement

3. **Business Risks** (2-3 bullets with mitigation):
   - Timeline delays → Mitigation: Phased approach, buffer time
   - Budget overruns → Mitigation: Cost monitoring, reserved capacity
   - Service disruption → Mitigation: Pilot testing, rollback procedures

**Format**: Markdown, 1,000 words MAX total (600 benefits + 400 risks), bullet points
**Tone**: Balanced, realistic, actionable
**CRITICAL**: 
- Project-specific risks only (avoid generic "cloud is new" type risks)
- Include mitigation for each risk
- Integrate EKS benefits/risks (not separate)
- IF MRA available, reference MRA findings in risk assessment
- **USE RECOMMENDED OPTION'S PRICING** (Option 3 if EKS recommended, otherwise Option 1/2)

**❌ FORBIDDEN IN OUTPUT**:
- NO meta-commentary
- NO word count mentions in section titles (e.g., "Benefits (600 words MAX)")
- NO instruction references
- Remove word limits from section headers
- Write ONLY the actual content
"""

RECOMMENDATIONS_PROMPT = """
🚨🚨🚨 STOP! READ THIS FIRST! 🚨🚨🚨

LOOK FOR "PRE-COMPUTED RVTOOLS SUMMARY" OR "EXACT COSTS FROM EXCEL FILE" IN YOUR CONTEXT.

DOES IT SAY "Total VMs" OR "Total Servers and Total Databases"?

IF "Total VMs" → THIS IS RVTOOLS = EC2 ONLY, NO DATABASES EXIST
  ❌ FORBIDDEN: RDS, Aurora, DynamoDB, DMS, database migration
  ❌ DO NOT recommend database services
  ✅ ALLOWED: Lambda, ECS, EKS, Fargate, EC2, Auto Scaling, S3

IF "Total Servers" AND "Total Databases" → THIS IS IT INVENTORY = EC2 + DATABASES
  ✅ ALLOWED: Include database recommendations

🚨 IF YOU WRITE "RDS" OR "DMS" WHEN INPUT SAYS "Total VMs", YOU FAILED 🚨

================================================================================

Generate a CONCISE Recommendations and Next Steps section.

**CONCISENESS REQUIREMENTS:**
- Maximum 600 words total
- Use bullet points and tables (not paragraphs)
- Focus on ACTIONABLE next steps only
- NO repetition of previous sections
- Reference Excel for detailed planning

**Input**: All previous analyses and recommendations including MRA (Migration Readiness Assessment).

**CRITICAL - MRA INTEGRATION**:
- IF MRA analysis available, use MRA findings to prioritize recommendations
- Reference MRA gaps in immediate actions (e.g., "Address skills gap identified in MRA")
- Use MRA readiness scores to inform training and enablement recommendations
- Example: "Implement cloud training program (MRA identified skills gap in cloud operations)"

**CRITICAL - DEPENDENCY MAPPING REQUIREMENT**:
- **MUST include** application dependency mapping as a critical next step
- Recommend AWS Application Discovery Service or AWS Migration Hub
- Explain that detailed wave planning requires dependency data
- This is NOT optional - it's a prerequisite for migration execution
  
- IF you see "Total Servers" AND "Total Databases" (IT Inventory): Include both compute and database recommendations

**CRITICAL - AVOID REPETITION**:
- Do NOT repeat cost savings numbers (already in Executive Summary and Cost Analysis)
- Do NOT repeat wave structures (already in Migration Roadmap)
- Do NOT repeat VM counts or infrastructure details (already in Current State)
- Do NOT repeat benefits or risks (already in Benefits section)
- Focus on NEW, ACTIONABLE next steps only

**CRITICAL - EKS INTEGRATION**:
- IF EKS analysis available, include EKS-specific recommendations
- Example: "Conduct container readiness assessment for [X] Linux VMs"
- Example: "Establish Kubernetes training program for operations team"
- Keep EKS recommendations integrated (not separate subsection)

**Generate** (concise - 600 words MAX):

### Strategic Recommendations (3 items max)
- Top 3 strategic priorities (NEW insights, not repeating previous sections)
- IF EKS available: Include container strategy recommendation
- IF MRA available: Reference MRA findings to prioritize strategic actions
- Focus on decision points and critical success factors

### Immediate Actions (5-7 bullets)
- Week 1-2 priorities
- Do NOT recommend assessments already completed (check context for completed assessments)
- IF EKS available: Include "Initiate container readiness assessment" if not done
- IF MRA available: Include actions to address critical MRA gaps
- Focus on actions that unblock migration start

### Recommended Deep-Dive Assessments (if applicable)
**CRITICAL**: Only recommend assessments NOT already completed

Check context for completed assessments:
- IF RVTools analysis provided → Do NOT recommend RVTools
- IF MRA analysis provided → Do NOT recommend MRA
- IF ATX analysis provided → Do NOT recommend ATX

**IF only basic data (RVTools) was provided, RECOMMEND**:
- **AWS Migration Evaluator**: Detailed TCO analysis and right-sizing recommendations
- **Migration Portfolio Assessment (MPA)**: Application dependency mapping and wave planning
- **ISV Migration Tools**: Evaluate third-party solutions for enhanced migration capabilities

**IF EKS analysis available, RECOMMEND**:
- **Container Readiness Assessment**: Evaluate application suitability for containerization
- **Kubernetes Training Program**: Upskill operations team on EKS and Kubernetes

### 90-Day Action Plan (table format)

**CRITICAL**: Use RELATIVE timeframes only (Week 1-2, Month 1, Quarter 1)
**CRITICAL**: Focus on ACTIONS, not metrics or data points
**CRITICAL**: IF MRA available, include actions to address MRA gaps

| Timeframe | Activity | Owner | Success Criteria |
|-----------|----------|-------|------------------|
| Week 1-2 | [Action] | [Team] | [Outcome] |
| Week 3-4 | [Action] | [Team] | [Outcome] |
| Month 2 | [Action] | [Team] | [Outcome] |
| Month 3 | [Action] | [Team] | [Outcome] |

**IF EKS available**: Include EKS-related actions in timeline:
- Example: "Month 2 | EKS cluster setup and pilot containerization | DevOps Team | 5 apps containerized"

**IF MRA available**: Include actions to address MRA gaps:
- Example: "Week 3-4 | Launch cloud training program (MRA skills gap) | HR/Training | 20 staff enrolled"

### Decision Points and Go/No-Go Criteria (3-5 bullets)
- Key decision points in next 90 days
- Go/No-Go criteria for each phase
- IF EKS available: Include container migration decision criteria
- IF MRA available: Include readiness criteria based on MRA findings

**CRITICAL REQUIREMENTS**:
- Do NOT recommend assessments already completed (check context)
- Focus on NEXT steps, not repeating what's done
- Use RELATIVE timeframes (Week 1-2, Month 1, Quarter 1) - NOT specific dates
- DO NOT use calendar dates (e.g., "November 25" or "Dec 2025")
- Integrate EKS recommendations (not separate subsection)
- IF MRA available, prioritize actions based on MRA gaps
- Keep total length under 600 words

**Format**: Markdown, 600 words MAX, bullet points and tables
**Tone**: Actionable, clear, prioritized, forward-looking
**CRITICAL**: 
- Use relative timeframes only (Week 1-2, Month 1, etc.)
- NO repetition of previous sections
- IF MRA available, reference MRA findings in recommendations

**❌ FORBIDDEN IN OUTPUT**:
- NO meta-commentary
- NO word count mentions
- NO instruction references
- Write ONLY the actual recommendations content
"""


def inject_key_financial_metrics(business_case, agent_results=None):
    """
    Inject Key Financial Metrics into Executive Summary to match recommended option
    
    Instead of relying on LLM to generate correct metrics (which varies), we directly
    inject the correct metrics based on the recommendation.
    
    Args:
        business_case: Generated business case markdown
        agent_results: Dictionary of agent results (to extract recommendation)
    
    Returns:
        Business case with injected Key Financial Metrics
    """
    import re
    
    try:
        if not agent_results:
            print("ℹ No agent results - Key Financial Metrics not injected")
            return business_case
        
        # Try to get cost data from multiple sources
        monthly = 0
        annual = 0
        three_year = 0
        pricing_note = "3-Year EC2 Instance Savings Plan"
        has_rds = False
        
        # Source 1: EKS analysis (preferred if available)
        if 'eks_analysis' in agent_results:
            eks_result = agent_results['eks_analysis']
            if hasattr(eks_result, 'result'):
                eks_data = eks_result.result
                recommendation_data = eks_data.get('recommendation', {})
                option1_costs = eks_data.get('option1_costs', {})
                hybrid_costs = eks_data.get('hybrid_costs', {})
                
                recommended_option = recommendation_data.get('recommended_option_name', '')
                
                # Determine which costs to use
                if 'Option 3' in recommended_option or 'Hybrid' in recommended_option:
                    monthly = hybrid_costs.get('total_monthly', 0)
                    annual = hybrid_costs.get('total_annual', 0)
                    three_year = hybrid_costs.get('total_3yr', 0)
                    pricing_note = "Hybrid approach (EC2 + EKS)"
                    has_rds = hybrid_costs.get('has_rds', False)
                    print(f"✓ Using Option 3 (Hybrid) costs for Key Financial Metrics")
                else:
                    monthly = option1_costs.get('total_monthly', 0)
                    annual = option1_costs.get('total_annual', 0)
                    three_year = option1_costs.get('total_3yr', 0)
                    has_rds = option1_costs.get('has_rds', False)
                    print(f"✓ Using Option 1 costs from EKS analysis for Key Financial Metrics")
        
        # Source 2: Cost agent results (fallback if no EKS)
        if monthly == 0 and 'agent_aws_cost_arr' in agent_results:
            cost_result = agent_results['agent_aws_cost_arr']
            if hasattr(cost_result, 'result'):
                cost_data = cost_result.result
                # Extract from cost agent output
                if isinstance(cost_data, dict):
                    monthly = cost_data.get('total_monthly', 0)
                    annual = cost_data.get('total_annual', 0)
                    three_year = cost_data.get('total_3yr', 0)
                    has_rds = cost_data.get('has_rds', False)
                    print(f"✓ Using cost agent results for Key Financial Metrics")
        
        if monthly == 0:
            print("ℹ No cost data available - Key Financial Metrics not injected")
            return business_case
        
        # Build the Key Financial Metrics section
        if has_rds:
            metrics_section = f"""## Key Financial Metrics
- Monthly AWS Cost: ${monthly:,.2f}
- Annual AWS Cost (ARR, including backup): ${annual:,.2f}
- 3-Year Total Cost: ${three_year:,.2f} (including RDS upfront fees)
- One-time RDS Upfront Fee: $130,531.00
*Pricing based on {pricing_note}*"""
        else:
            metrics_section = f"""## Key Financial Metrics
- Monthly AWS Cost: ${monthly:,.2f}
- Annual AWS Cost (ARR, including backup): ${annual:,.2f}
- 3-Year Total Cost: ${three_year:,.2f}
*Pricing based on {pricing_note}*"""
        
        # Find and replace ANY Key Financial Metrics section (regardless of format)
        # Look for the section header and replace everything until the next ## header
        pattern = r'## Key Financial Metrics\n.*?(?=\n## |\n---|\Z)'
        
        if re.search(pattern, business_case, re.DOTALL):
            business_case = re.sub(pattern, metrics_section + '\n', business_case, count=1, flags=re.DOTALL)
            print(f"✓ Injected Key Financial Metrics section")
        else:
            print("ℹ Could not find Key Financial Metrics section to replace")
        
        return business_case
        
    except Exception as e:
        print(f"Warning: Could not inject Key Financial Metrics: {e}")
        import traceback
        traceback.print_exc()
        return business_case


def inject_current_state_highlights(business_case, rvtools_summary=None, it_inventory_summary=None, atx_summary=None):
    """
    Inject Current State Highlights into Executive Summary with deterministic data
    
    Replaces LLM-generated (potentially hallucinated) infrastructure details with
    actual data from the analysis.
    
    Args:
        business_case: Generated business case markdown
        rvtools_summary: RVTools summary dict (if available)
        it_inventory_summary: IT Inventory summary dict (if available)
        atx_summary: ATX summary dict (if available)
    
    Returns:
        Business case with injected Current State Highlights
    """
    import re
    
    try:
        # Determine which summary to use
        summary = rvtools_summary or it_inventory_summary or atx_summary
        
        if not summary:
            print("ℹ No infrastructure summary available - Current State Highlights not injected")
            return business_case
        
        # Build the Current State Highlights section based on available data
        highlights = []
        
        if rvtools_summary:
            # RVTools input
            total_vms = rvtools_summary.get('total_vms', 0)
            windows_vms = rvtools_summary.get('windows_vms', 0)
            linux_vms = rvtools_summary.get('linux_vms', 0)
            storage_tb = rvtools_summary.get('total_storage_tb', 0)
            
            highlights.append(f"Infrastructure spans {total_vms} virtual machines ({windows_vms} Windows, {linux_vms} Linux)")
            if storage_tb > 0:
                highlights.append(f"Total storage capacity: {storage_tb:.1f}TB")
                
        elif it_inventory_summary:
            # IT Inventory input
            total_servers = it_inventory_summary.get('total_vms', 0)
            total_databases = it_inventory_summary.get('total_databases', 0)
            windows_servers = it_inventory_summary.get('windows_vms', 0)
            linux_servers = it_inventory_summary.get('linux_vms', 0)
            storage_tb = it_inventory_summary.get('total_storage_tb', 0)
            
            highlights.append(f"Infrastructure includes {total_servers} servers and {total_databases} databases")
            if windows_servers > 0 or linux_servers > 0:
                highlights.append(f"Server distribution: {windows_servers} Windows, {linux_servers} Linux")
            if storage_tb > 0:
                highlights.append(f"Total storage capacity: {storage_tb:.1f}TB")
                
        elif atx_summary:
            # ATX input
            total_vms = atx_summary.get('total_vms', 0)
            windows_vms = atx_summary.get('windows_vms', 0)
            linux_vms = atx_summary.get('linux_vms', 0)
            storage_tb = atx_summary.get('total_storage_tb', 0)
            
            highlights.append(f"Infrastructure spans {total_vms} virtual machines")
            if windows_vms > 0 or linux_vms > 0:
                highlights.append(f"OS distribution: {windows_vms} Windows, {linux_vms} Linux")
            if storage_tb > 0:
                highlights.append(f"Total storage capacity: {storage_tb:.1f}TB")
        
        if not highlights:
            print("ℹ No infrastructure details available - Current State Highlights not injected")
            return business_case
        
        # Build the replacement section
        highlights_text = "\n".join(f"- {h}" for h in highlights)
        highlights_section = f"""**Current State Highlights**

{highlights_text}"""
        
        # Find and replace the Current State Highlights section
        # Pattern matches "**Current State Highlights**" followed by content until next section
        pattern = r'\*\*Current State Highlights\*\*\n\n.*?(?=\n\n\*\*|\n\n##|\Z)'
        
        if re.search(pattern, business_case, re.DOTALL):
            business_case = re.sub(pattern, highlights_section, business_case, count=1, flags=re.DOTALL)
            print(f"✓ Injected Current State Highlights with deterministic data")
        else:
            print("ℹ Could not find Current State Highlights section to replace")
        
        return business_case
        
    except Exception as e:
        print(f"Warning: Could not inject Current State Highlights: {e}")
        import traceback
        traceback.print_exc()
        return business_case


def remove_duplicate_cost_analysis_section(business_case):
    """
    Remove duplicate H1 "# Cost Analysis" section that LLM generates
    
    The LLM often generates a full "# Cost Analysis" section with detailed
    Option 1/2/3 listings, even when we tell it not to. This creates duplication
    with the "## Cost Analysis and TCO" section where we inject the table.
    
    This function removes the H1 section, keeping only the H2 section where
    the table will be injected.
    
    Args:
        business_case: Generated business case markdown
    
    Returns:
        Business case with duplicate H1 Cost Analysis section removed
    """
    import re
    
    try:
        # Pattern to match "# Cost Analysis" (H1) and everything until next H1 section
        # This removes the LLM-generated duplicate section
        pattern = r'\n# Cost Analysis\n.*?(?=\n# |\Z)'
        
        # Check if pattern exists
        if re.search(pattern, business_case, flags=re.DOTALL):
            business_case = re.sub(pattern, '', business_case, flags=re.DOTALL)
            print("✓ Removed duplicate H1 Cost Analysis section")
        else:
            print("ℹ No duplicate H1 Cost Analysis section found")
        
        return business_case
        
    except Exception as e:
        print(f"Warning: Could not remove duplicate Cost Analysis section: {e}")
        return business_case


def inject_pricing_comparison_table(business_case, agent_results=None):
    """
    Post-process the business case to inject a 3-column comparison table
    showing Option 1, Option 2, and Option 3 (Hybrid) side-by-side
    Uses actual recommendation logic from EKS analysis (dynamic, not static)
    
    Args:
        business_case: Generated business case markdown
        agent_results: Dictionary of agent results (to extract EKS recommendation)
    
    Returns:
        Business case with pricing comparison table injected
    """
    try:
        # Try to find Excel file in case-specific output folder first
        from agents.utils.project_context import get_case_id
        case_id = get_case_id()
        excel_files = []
        is_rvtools = False
        
        if case_id:
            case_output_dir = os.path.join(output_folder_dir_path, case_id)
            if os.path.exists(case_output_dir):
                # Try IT Inventory in case folder first
                excel_files = glob.glob(os.path.join(case_output_dir, 'it_inventory_aws_pricing_*.xlsx'))
                if not excel_files:
                    # Try RVTools in case folder
                    rvtools_path = os.path.join(case_output_dir, 'vm_to_ec2_mapping.xlsx')
                    if os.path.exists(rvtools_path):
                        excel_files = [rvtools_path]
                        is_rvtools = True
        
        # Fallback to root output folder if not found in case folder
        if not excel_files:
            excel_files = glob.glob(os.path.join(output_folder_dir_path, 'it_inventory_aws_pricing_*.xlsx'))
            if not excel_files:
                # Try RVTools in root folder
                rvtools_path = os.path.join(output_folder_dir_path, 'vm_to_ec2_mapping.xlsx')
                if os.path.exists(rvtools_path):
                    excel_files = [rvtools_path]
                    is_rvtools = True
        
        if not excel_files:
            # No pricing file found - show explicit message rather than empty section
            print("⚠ No pricing Excel file found for table injection")
            # Inject a clear message in the Cost Analysis section
            cost_section_header = "## Cost Analysis and TCO"
            if cost_section_header in business_case:
                cost_message = f"""{cost_section_header}

**⚠️ Cost Analysis Unavailable**

The deterministic pricing calculation could not be completed for this run. This can occur when the cost analysis agent exceeds processing limits due to environment complexity.

**To resolve this issue:**
- Re-run the business case generation (the issue is typically transient)
- If the problem persists, contact your administrator to check Bedrock service quotas

**Note:** All cost figures in this document (Executive Summary, EKS analysis) are derived from the deterministic pricing engine when available. Without the completed pricing calculation, cost figures shown elsewhere in this document may be from a previous successful run or estimated values.

---
"""
                business_case = business_case.replace(cost_section_header + "\n\n\n", cost_message)
                business_case = business_case.replace(cost_section_header + "\n\n---", cost_message + "\n---")
            return business_case
        
        latest_excel = max(excel_files, key=os.path.getmtime) if len(excel_files) > 1 else excel_files[0]
        print(f"✓ Using Excel file for table: {os.path.basename(latest_excel)} (RVTools: {is_rvtools})")
        
        # Read the Pricing_Comparison sheet (name differs by input type)
        sheet_name = 'Pricing_Comparison' if not is_rvtools else 'Pricing Comparison'
        df = pd.read_excel(latest_excel, sheet_name=sheet_name)
        
        # Extract values for Option 1 and Option 2
        values = df['Value'].tolist()
        
        # Parse values based on input type
        if is_rvtools:
            # RVTools format (no RDS, simpler structure)
            # NEW STRUCTURE (with backup costs):
            # Row 3: EC2 Monthly (Compute + Storage)
            # Row 4: Backup Monthly
            # Row 5: Total Monthly (incl. Backup)
            # Row 6: Total Annual (ARR)
            # Row 7: 3-Year Pricing
            # Row 10: Option 2 EC2 Monthly
            # Row 11: Option 2 Backup Monthly
            # Row 12: Option 2 Total Monthly
            # Row 13: Option 2 Annual
            # Row 14: Option 2 3-Year
            opt1_ec2_monthly = 'N/A'
            opt1_rds_monthly = 'N/A'
            opt1_total_monthly = values[5] if len(values) > 5 else 'N/A'  # Total Monthly (incl. Backup)
            opt1_annual = values[6] if len(values) > 6 else 'N/A'  # Total Annual (ARR)
            opt1_3year = values[7] if len(values) > 7 else 'N/A'  # 3-Year Pricing
            opt1_rds_upfront = 'N/A'
            opt1_3year_incl = 'N/A'
            
            opt2_ec2_monthly = 'N/A'
            opt2_rds_monthly = 'N/A'
            opt2_total_monthly = values[12] if len(values) > 12 else 'N/A'  # Option 2 Total Monthly
            opt2_annual = values[13] if len(values) > 13 else 'N/A'  # Option 2 Annual
            opt2_3year = values[14] if len(values) > 14 else 'N/A'  # Option 2 3-Year
            opt2_rds_upfront = 'N/A'
            opt2_3year_incl = 'N/A'
        else:
            # IT Inventory format (has RDS)
            opt1_ec2_monthly = values[4] if len(values) > 4 else 'N/A'
            opt1_rds_monthly = values[5] if len(values) > 5 else 'N/A'
            opt1_total_monthly = values[6] if len(values) > 6 else 'N/A'
            opt1_annual = values[7] if len(values) > 7 else 'N/A'
            opt1_3year = values[8] if len(values) > 8 else 'N/A'
            opt1_rds_upfront = values[9] if len(values) > 9 else 'N/A'
            opt1_3year_incl = values[10] if len(values) > 10 else 'N/A'
            
            opt2_ec2_monthly = values[13] if len(values) > 13 else 'N/A'
            opt2_rds_monthly = values[14] if len(values) > 14 else 'N/A'
            opt2_total_monthly = values[15] if len(values) > 15 else 'N/A'
            opt2_annual = values[16] if len(values) > 16 else 'N/A'
            opt2_3year = values[17] if len(values) > 17 else 'N/A'
            opt2_rds_upfront = values[18] if len(values) > 18 else 'N/A'
            opt2_3year_incl = values[19] if len(values) > 19 else 'N/A'
        
        # Extract Option 3 (Hybrid) costs and recommendation from agent_results
        opt3_ec2_monthly = 'N/A'
        opt3_eks_monthly = 'N/A'
        opt3_rds_monthly = 'N/A'
        opt3_total_monthly = 'N/A'
        opt3_annual = 'N/A'
        opt3_3year = 'N/A'
        recommendation_data = None
        
        # Get EKS analysis results if available
        if agent_results and 'eks_analysis' in agent_results:
            try:
                eks_result = agent_results['eks_analysis']
                if hasattr(eks_result, 'result'):
                    eks_data = eks_result.result
                    hybrid_costs = eks_data.get('hybrid_costs', {})
                    recommendation_data = eks_data.get('recommendation', {})
                    
                    # Extract Option 3 costs
                    opt3_ec2_monthly = f"${hybrid_costs.get('ec2_monthly', 0):,.2f}"
                    opt3_eks_monthly = f"${hybrid_costs.get('eks_monthly', 0):,.2f}"
                    opt3_total_monthly = f"${hybrid_costs.get('total_monthly', 0):,.2f}"
                    opt3_annual = f"${hybrid_costs.get('total_annual', 0):,.2f}"
                    opt3_3year = f"${hybrid_costs.get('total_3yr', 0):,.2f}"
                    
                    # If Option 3 has RDS, use Option 1's RDS cost
                    if hybrid_costs.get('has_rds', False) and opt1_rds_monthly != 'N/A':
                        opt3_rds_monthly = opt1_rds_monthly
            except Exception as e:
                print(f"Warning: Could not extract EKS data: {e}")
        
        # Build the 3-column comparison table
        table_markdown = "\n\n## AWS Cost Summary\n\n"
        table_markdown += "The following table provides a side-by-side comparison of all three pricing options:\n\n"
        
        # Build table header based on input type
        if is_rvtools:
            # RVTools: No RDS, simpler headers
            table_markdown += "| Cost Component | Option 1: EC2 Instance SP (3yr) | Option 2: Compute SP (3yr) | Option 3: Hybrid (EC2 + EKS) |\n"
            table_markdown += "|----------------|----------------------------------|----------------------------|------------------------------|\n"
        else:
            # IT Inventory: Has RDS
            table_markdown += "| Cost Component | Option 1: EC2 Instance SP (3yr) + RDS Partial Upfront | Option 2: Compute SP (3yr) + RDS No Upfront (1yr×3) | Option 3: Hybrid (EC2 + EKS + RDS) |\n"
            table_markdown += "|----------------|------------------------------------------------------|-----------------------------------------------------|------------------------------------|\n"
        
        # Add EC2 row only if we have separate EC2 costs (IT Inventory)
        if not is_rvtools and opt1_ec2_monthly != 'N/A':
            table_markdown += f"| **EC2 Monthly Cost** | {opt1_ec2_monthly} | {opt2_ec2_monthly} | {opt3_ec2_monthly} |\n"
        
        # Add EKS line only for Option 3
        if opt3_eks_monthly != 'N/A':
            table_markdown += f"| **EKS Monthly Cost** | - | - | {opt3_eks_monthly} |\n"
        
        # Add RDS line if applicable (IT Inventory only)
        if not is_rvtools and opt1_rds_monthly != 'N/A' and opt1_rds_monthly != '$0.00':
            table_markdown += f"| **RDS Monthly Cost** | {opt1_rds_monthly} | {opt2_rds_monthly} | {opt3_rds_monthly} |\n"
        
        table_markdown += f"| **Total Monthly Cost** | {opt1_total_monthly} | {opt2_total_monthly} | {opt3_total_monthly} |\n"
        table_markdown += f"| **Total Annual Cost (ARR)** | {opt1_annual} | {opt2_annual} | {opt3_annual} |\n"
        table_markdown += f"| **3-Year Total (monthly only)** | {opt1_3year} | {opt2_3year} | {opt3_3year} |\n"
        
        # Add upfront fees if applicable (IT Inventory with RDS only)
        if not is_rvtools and opt1_rds_upfront != 'N/A' and opt1_rds_upfront != '$0.00':
            table_markdown += f"| **RDS Upfront Fees (one-time)** | {opt1_rds_upfront} | {opt2_rds_upfront} | {opt1_rds_upfront} |\n"
            table_markdown += f"| **3-Year Total (incl. upfront)** | {opt1_3year_incl} | {opt2_3year_incl} | {opt3_3year} |\n"
        
        # Add notes based on input type
        table_markdown += "\n**Notes:**\n"
        if is_rvtools:
            # RVTools notes (no RDS)
            table_markdown += "- Option 1: 3-Year EC2 Instance Savings Plan (lowest cost)\n"
            table_markdown += "- Option 2: 3-Year Compute Savings Plan (higher cost, more flexibility)\n"
            table_markdown += "- Option 3: Hybrid approach with containerization for suitable workloads (strategic value)\n"
        else:
            # IT Inventory notes (with RDS)
            table_markdown += "- Option 1: 3-Year EC2 Instance Savings Plan + 3-Year RDS Partial Upfront (lowest cost)\n"
            table_markdown += "- Option 2: 3-Year Compute Savings Plan + 1-Year RDS No Upfront renewed 3 times (highest cost)\n"
            table_markdown += "- Option 3: Hybrid approach with containerization for suitable workloads (strategic value)\n"
        table_markdown += "- All costs include AWS Backup with intelligent tiering\n"
        
        # Add dynamic recommendation section based on actual EKS recommendation logic
        if recommendation_data:
            recommended_option_name = recommendation_data.get('recommended_option_name', '')
            tier_name = recommendation_data.get('tier_name', '')
            rationale_text = recommendation_data.get('rationale_text', '')
            cost_diff_dollars = recommendation_data.get('cost_difference_dollars', 0)
            cost_diff_pct = recommendation_data.get('cost_difference_pct', 0)
            
            table_markdown += f"\n### Recommended Option\n\n"
            table_markdown += f"**{recommended_option_name}**\n\n"
            table_markdown += f"**Decision Tier:** {tier_name}\n\n"
            table_markdown += f"**Rationale:** {rationale_text}\n\n"
            
            # Add cost comparison details
            if 'Option 3' in recommended_option_name or 'Hybrid' in recommended_option_name:
                if cost_diff_pct > 0:
                    table_markdown += f"**Cost Premium:** ${abs(cost_diff_dollars):,.2f} over 3 years ({cost_diff_pct:.2f}% premium)\n\n"
                    table_markdown += f"**Strategic Value:** The premium is justified by containerization benefits, improved scalability, DevOps velocity, and modernization capabilities.\n"
                else:
                    table_markdown += f"**Cost Savings:** ${abs(cost_diff_dollars):,.2f} over 3 years ({abs(cost_diff_pct):.2f}% savings)\n\n"
                    table_markdown += f"**Additional Benefits:** Plus containerization, scalability, and modernization advantages.\n"
            elif 'Option 1' in recommended_option_name:
                try:
                    # Calculate savings vs Option 2
                    opt1_monthly_val = float(str(opt1_total_monthly).replace('$', '').replace(',', ''))
                    opt2_monthly_val = float(str(opt2_total_monthly).replace('$', '').replace(',', ''))
                    monthly_savings = opt2_monthly_val - opt1_monthly_val
                    threeyear_savings = monthly_savings * 36
                    savings_pct = (monthly_savings / opt2_monthly_val * 100) if opt2_monthly_val > 0 else 0
                    
                    table_markdown += f"**Cost Savings:** ${monthly_savings:,.2f}/month vs Option 2 ({savings_pct:.1f}% savings)\n"
                    table_markdown += f"**3-Year Savings:** ${threeyear_savings:,.2f} over 3 years\n"
                except:
                    pass
        
        # Add note about Excel files (different for IT Inventory vs RVTools)
        table_markdown += "\n**📊 Detailed Analysis Available:**\n"
        
        # Detect if we have RDS data (for Excel file note)
        has_rds = not is_rvtools and opt1_rds_monthly != 'N/A' and opt1_rds_monthly != '$0.00'
        eks_monthly = opt3_eks_monthly != 'N/A'
        
        if has_rds:
            # IT Inventory input
            table_markdown += "- **IT Inventory Pricing Excel** (`it_inventory_aws_pricing_*.xlsx`): Server-by-server and database-by-database cost breakdown, instance type mappings, storage costs, RDS configurations, and backup costs\n"
        else:
            # RVTools input
            table_markdown += "- **VM to EC2 Mapping Excel** (`vm_to_ec2_mapping.xlsx`): VM-by-VM cost breakdown, EC2 instance type mappings, compute and storage costs, and backup costs\n"
        
        # EKS analysis is available for both input types
        if eks_monthly:
            table_markdown += "- **EKS Migration Analysis Excel** (`eks_migration_analysis.xlsx`): EKS cluster design, VM categorization, capacity planning, and ROI analysis\n"
        
        table_markdown += "- See Excel files for complete VM-level details, right-sizing recommendations, and cost optimization opportunities\n"
        
        # Add Migration Cost Ramp section
        table_markdown += "\n## Migration Cost Ramp\n\n"
        
        # Get project timeline from agent_results or use default
        timeline_months = 12  # Default
        if agent_results and 'agent_migration_plan' in agent_results:
            try:
                plan_result = agent_results['agent_migration_plan']
                if hasattr(plan_result, 'result'):
                    plan_data = plan_result.result
                    if isinstance(plan_data, dict):
                        timeline_months = plan_data.get('timeline_months', 12)
                    elif isinstance(plan_data, str) and 'month' in plan_data.lower():
                        # Try to extract timeline from text
                        import re
                        match = re.search(r'(\d+)[-\s]month', plan_data.lower())
                        if match:
                            timeline_months = int(match.group(1))
            except:
                pass
        
        # Determine which option's cost to use for ramp
        ramp_monthly_cost = opt1_total_monthly  # Default to Option 1
        ramp_option_name = "Option 1"
        
        if recommendation_data:
            recommended_option = recommendation_data.get('recommended_option_name', '')
            if 'Option 3' in recommended_option or 'Hybrid' in recommended_option:
                ramp_monthly_cost = opt3_total_monthly
                ramp_option_name = "Option 3 (Hybrid)"
            elif 'Option 2' in recommended_option:
                ramp_monthly_cost = opt2_total_monthly
                ramp_option_name = "Option 2"
        
        # Convert monthly cost to float for calculations
        try:
            ramp_cost_val = float(str(ramp_monthly_cost).replace('$', '').replace(',', ''))
        except:
            ramp_cost_val = 0
        
        # Calculate ramp based on timeline
        if timeline_months <= 6:
            # 3-6 month timeline
            phase1_pct = 0.30
            phase2_pct = 0.70
            phase3_pct = 1.00
            phase1_label = f"Months 1-{timeline_months//2}"
            phase2_label = f"Months {timeline_months//2+1}-{timeline_months}"
            table_markdown += f"Based on {timeline_months}-month migration timeline using {ramp_option_name} pricing:\n\n"
            table_markdown += f"- **{phase1_label}**: ${ramp_cost_val * phase1_pct:,.2f}/month (30% of workloads)\n"
            table_markdown += f"- **{phase2_label}**: ${ramp_cost_val * phase2_pct:,.2f}/month (70% of workloads)\n"
        elif timeline_months <= 12:
            # 7-12 month timeline
            phase1_pct = 0.30
            phase2_pct = 0.70
            phase3_pct = 1.00
            phase1_end = timeline_months // 3
            phase2_end = (timeline_months * 2) // 3
            table_markdown += f"Based on {timeline_months}-month migration timeline using {ramp_option_name} pricing:\n\n"
            table_markdown += f"- **Months 1-{phase1_end}**: ${ramp_cost_val * phase1_pct:,.2f}/month (30% of workloads)\n"
            table_markdown += f"- **Months {phase1_end+1}-{phase2_end}**: ${ramp_cost_val * phase2_pct:,.2f}/month (70% of workloads)\n"
            table_markdown += f"- **Months {phase2_end+1}-{timeline_months}**: ${ramp_cost_val * phase3_pct:,.2f}/month (100% of workloads)\n"
        else:
            # 13+ month timeline
            phase1_pct = 0.30
            phase2_pct = 0.70
            phase3_pct = 1.00
            phase1_end = timeline_months // 3
            phase2_end = (timeline_months * 2) // 3
            table_markdown += f"Based on {timeline_months}-month migration timeline using {ramp_option_name} pricing:\n\n"
            table_markdown += f"- **Months 1-{phase1_end}**: ${ramp_cost_val * phase1_pct:,.2f}/month (30% of workloads)\n"
            table_markdown += f"- **Months {phase1_end+1}-{phase2_end}**: ${ramp_cost_val * phase2_pct:,.2f}/month (70% of workloads)\n"
            table_markdown += f"- **Months {phase2_end+1}-{timeline_months}**: ${ramp_cost_val * phase3_pct:,.2f}/month (100% of workloads)\n"
        
        # Add Business Value Justification section
        table_markdown += "\n## Business Value Justification\n\n"
        table_markdown += "Beyond cost considerations, AWS migration delivers strategic business value:\n\n"
        table_markdown += "- **Agility and Speed**: Rapid provisioning and deployment capabilities accelerate time-to-market\n"
        table_markdown += "- **Innovation Platform**: Access to 200+ AWS services enables new capabilities (AI/ML, analytics, IoT)\n"
        table_markdown += "- **Scalability**: Elastic infrastructure scales with business growth without upfront investment\n"
        table_markdown += "- **Operational Excellence**: Managed services reduce operational overhead and improve reliability\n"
        table_markdown += "- **Security and Compliance**: Enterprise-grade security controls and compliance certifications\n"
        table_markdown += "- **Global Reach**: Deploy applications closer to customers with AWS global infrastructure\n"
        
        if eks_monthly:
            table_markdown += "- **Modernization**: Container-based architecture with EKS enables DevOps practices and microservices\n"
        
        table_markdown += "- **Reduced Technical Debt**: Eliminate aging infrastructure and legacy technology constraints\n"
        table_markdown += "- **Focus on Core Business**: Shift IT resources from infrastructure management to innovation\n"
        
        # Replace the empty Cost Analysis section with our table
        # The section was created as empty placeholder by generate_multi_stage_business_case()
        import re
        
        # Pattern to find "## Cost Analysis and TCO" section and replace its content
        # Match from the header to the next "---" or "##" section marker
        # Be flexible with whitespace (handle multiple newlines)
        pattern = r'(## Cost Analysis and TCO\n+)(.*?)(\n*---\n|\n## )'
        
        def replace_cost_analysis(match):
            header = match.group(1)
            separator = match.group(3)
            # Return header + our table + separator (normalize to single newline before separator)
            return header + '\n' + table_markdown + '\n' + separator
        
        if re.search(pattern, business_case, flags=re.DOTALL):
            business_case = re.sub(pattern, replace_cost_analysis, business_case, count=1, flags=re.DOTALL)
            print(f"✓ Cost Analysis section populated with pricing comparison table")
        else:
            # Fallback: try to inject before Assumptions or Migration Roadmap
            print("⚠ Could not find Cost Analysis section, trying fallback injection")
            patterns = [
                (r'(## Assumptions)', r'\n## Cost Analysis and TCO\n\n' + table_markdown + r'\n---\n\n\1'),
                (r'(## Migration)', r'\n## Cost Analysis and TCO\n\n' + table_markdown + r'\n---\n\n\1'),
            ]
            
            injected = False
            for pattern, replacement in patterns:
                if re.search(pattern, business_case):
                    business_case = re.sub(pattern, replacement, business_case, count=1)
                    injected = True
                    print(f"✓ Cost Analysis section created and populated (fallback)")
                    break
            
            if not injected:
                print("✗ Could not inject Cost Analysis section")
        
        return business_case
        
    except Exception as e:
        print(f"Warning: Could not inject Pricing Comparison table: {e}")
        import traceback
        traceback.print_exc()
        return business_case


def generate_multi_stage_business_case(agent_results, project_context, rvtools_summary=None, it_inventory_summary=None, atx_summary=None, mra_summary=None):
    """
    Generate business case in multiple stages for maximum quality

    Args:
        agent_results: Dictionary of results from all agents
        project_context: Project information and context
        rvtools_summary: RVTools summary dict (if available)
        it_inventory_summary: IT Inventory summary dict (if available)
        atx_summary: ATX summary dict (if available)
        mra_summary: Raw MRA text already read from disk. Used when the MRA
            agent fails, so a failed node cannot make the document assert that
            no assessment was supplied.

    Returns:
        Complete business case document
    """
    print("="*80)
    print("MULTI-STAGE BUSINESS CASE GENERATION")
    print("="*80)
    
    sections = {}
    
    # Prepare context for all sections
    # Extract actual results from NodeResult objects
    def get_result_text(node_id, max_chars=3000):
        """
        Extract result text with size limit to prevent context overflow.
        Reduced from 8000 to 3000 chars to prevent max_tokens errors.
        """
        if node_id in agent_results:
            result = agent_results[node_id].result
            if result:
                result_text = str(result)
                # Extract key metrics from the beginning (usually has summary)
                # Take first N chars to ensure we capture the important numbers
                # while keeping context size manageable
                return result_text[:max_chars]
        return 'N/A'
    
    def get_mra_content():
        """Get MRA content, in order of preference.

        The agent's synthesis is best, but that node can fail — it raised
        MaxTokensReachedException on a 16k-character MRA — and when it did, the
        document claimed no MRA had been supplied even though one had. So fall
        back to the document text Python already read before declaring it
        missing. Same idea as the pre-computed RVTools and IT Inventory
        summaries: do not let an LLM failure turn into a factual claim.
        """
        if 'agent_mra_analysis' in agent_results and agent_results['agent_mra_analysis'].result:
            return str(agent_results['agent_mra_analysis'].result)
        elif mra_summary:
            return (
                "**Migration Readiness Assessment (MRA) — source document**\n\n"
                "The MRA below is quoted from the supplied assessment. Summarise its "
                "findings; do not state that an MRA was not provided.\n\n"
                f"{mra_summary}"
            )
        else:
            return """**Migration Readiness Assessment (MRA) Not Provided**

A Migration Readiness Assessment was not included in the input data. We strongly recommend conducting a comprehensive MRA to evaluate organizational readiness across the following dimensions:

**Key Assessment Areas:**
- **Business**: Strategic alignment, executive sponsorship, business case clarity
- **People**: Skills assessment, training needs, change management readiness
- **Process**: Governance, migration methodology, operational procedures
- **Technology**: Current state architecture, technical debt, cloud readiness
- **Security**: Compliance requirements, security controls, risk management
- **Operations**: Monitoring, incident management, operational excellence
- **Financial**: Cost management, budgeting, financial governance

**Benefits of Conducting an MRA:**
- Identifies organizational gaps and risks early
- Provides actionable recommendations to improve readiness
- Establishes baseline metrics for measuring progress
- Reduces migration risks and accelerates timeline
- Improves stakeholder alignment and buy-in

**Recommendation:** Engage with AWS Professional Services or an AWS Partner to conduct a formal Migration Readiness Assessment before proceeding with large-scale migration activities."""
    
    # Determine which assessments were completed
    completed_assessments = []
    if 'agent_rv_tool_analysis' in agent_results and agent_results['agent_rv_tool_analysis'].result:
        completed_assessments.append('RVTools VMware Assessment')
    if 'agent_atx_analysis' in agent_results and agent_results['agent_atx_analysis'].result:
        completed_assessments.append('AWS Transform (ATX) Assessment')
    if 'agent_mra_analysis' in agent_results and agent_results['agent_mra_analysis'].result:
        completed_assessments.append('Migration Readiness Assessment (MRA)')
    if 'agent_it_analysis' in agent_results and agent_results['agent_it_analysis'].result:
        completed_assessments.append('IT Infrastructure Inventory Analysis')
    
    # Extract EKS analysis if available AND enabled
    eks_context = ""
    eks_enabled = EKS_CONFIG.get('enabled', False)
    
    # Get recommendation thresholds from config
    from agents.config.config import EKS_RECOMMENDATION_THRESHOLDS
    tier2_threshold = EKS_RECOMMENDATION_THRESHOLDS.get('tier2_max_premium', 0.20)
    tier3_threshold = EKS_RECOMMENDATION_THRESHOLDS.get('tier3_max_premium', 0.50)
    tier2_pct = int(tier2_threshold * 100)
    tier3_pct = int(tier3_threshold * 100)
    
    if eks_enabled and 'eks_analysis' in agent_results and agent_results['eks_analysis'].result:
        eks_data = agent_results['eks_analysis'].result
        vm_cat = eks_data.get('vm_categorization', {})
        vm_cat_summary = vm_cat.get('summary', {})
        strategy_rec = eks_data.get('strategy_recommendation', {})
        eks_costs_data = eks_data.get('eks_costs', {})
        cluster_config = eks_data.get('cluster_config', {})
        hybrid_costs = eks_data.get('hybrid_costs', {})
        
        # Calculate totals
        eks_linux_vms = vm_cat.get('eks_linux', [])
        eks_windows_vms = vm_cat.get('eks_windows', [])
        ec2_vms = vm_cat.get('ec2', [])
        
        eks_linux_vcpu = sum(vm.get('vcpu', 0) for vm in eks_linux_vms)
        ec2_vcpu = sum(vm.get('vcpu', 0) for vm in ec2_vms)
        
        eks_context = f"""

**═══════════════════════════════════════════════════════════════**
**EKS MIGRATION ANALYSIS AVAILABLE**
**═══════════════════════════════════════════════════════════════**

**Recommended Strategy:** {strategy_rec.get('recommended_strategy', 'hybrid').upper()}
**Confidence Level:** {strategy_rec.get('confidence', 'medium').upper()}

**VM Categorization:**
- Linux VMs → EKS: {vm_cat_summary.get('eks_linux', 0)} VMs ({eks_linux_vcpu} vCPU)
- Windows VMs → EKS: {vm_cat_summary.get('eks_windows', 0)} VMs
- VMs → EC2: {vm_cat_summary.get('ec2_total', 0)} VMs ({ec2_vcpu} vCPU)
- Total VMs: {vm_cat_summary.get('total_vms', 0)}

**EKS Cluster Configuration:**
- Worker Nodes: {len(cluster_config.get('all_worker_nodes', []))}
- Total vCPU Capacity: {sum(n.get('vcpu', 0) for n in cluster_config.get('all_worker_nodes', []))}
- Required vCPU: {cluster_config.get('required_vcpu', 0):.1f}
- Required Memory: {cluster_config.get('required_memory', 0):.1f} GB

**HYBRID COST BREAKDOWN (EC2 + EKS):**
- EC2 Monthly ({hybrid_costs.get('ec2_vm_count', 0)} VMs): ${hybrid_costs.get('ec2_monthly', 0):,.2f}
- EKS Monthly ({hybrid_costs.get('eks_vm_count', 0)} VMs): ${hybrid_costs.get('eks_monthly', 0):,.2f}
- **HYBRID TOTAL MONTHLY: ${hybrid_costs.get('total_monthly', 0):,.2f}**
- **HYBRID TOTAL ANNUAL: ${hybrid_costs.get('total_annual', 0):,.2f}**
- **HYBRID TOTAL 3-YEAR: ${hybrid_costs.get('total_3yr', 0):,.2f}**

**EKS Component Costs:**
- Control Plane (3yr): ${eks_costs_data.get('control_plane_3yr', 0):,.2f}
- Worker Nodes (3yr): ${eks_costs_data.get('workers_3yr', 0):,.2f}
- Storage (3yr): ${eks_costs_data.get('storage_3yr', 0):,.2f}
- Networking (3yr): ${eks_costs_data.get('networking_3yr', 0):,.2f}

**Rationale:** {strategy_rec.get('rationale', 'No rationale provided')}

**Timeline:** {strategy_rec.get('timeline_months', 12)} months

**═══════════════════════════════════════════════════════════════**
**EKS RECOMMENDATION (PRE-CALCULATED - DO NOT CHANGE)**
**═══════════════════════════════════════════════════════════════**

The recommendation has been calculated using deterministic Python logic.
DO NOT apply your own logic - simply use the recommendation provided below.

**Recommended Option:** {eks_data.get('recommendation', {}).get('recommended_option_name', 'N/A')}
**Tier:** {eks_data.get('recommendation', {}).get('tier_name', 'N/A')}
**Cost Difference:** ${eks_data.get('recommendation', {}).get('cost_difference_dollars', 0):,.2f} ({eks_data.get('recommendation', {}).get('cost_difference_pct', 0):.2f}%)
**Rationale Text:** {eks_data.get('recommendation', {}).get('rationale_text', 'N/A')}

**YOUR TASK:**
- Copy the "Recommended Option" EXACTLY as shown above
- Use the "Rationale Text" provided (you may expand slightly for clarity)
- DO NOT change the recommendation based on your own cost analysis
- The decision is final and deterministic

**═══════════════════════════════════════════════════════════════**

**RECOMMENDATION THRESHOLDS (from config.py):**
- Tier 2 Max Premium: {tier2_pct}% (recommend EKS up to {tier2_pct}% premium)
- Tier 3 Max Premium: {tier3_pct}% (mention EKS as alternative up to {tier3_pct}% premium)
- Apply 4-tier decision tree:
  * Tier 1: EKS < EC2 → Recommend EKS
  * Tier 2: EKS 0-{tier2_pct}% more → Recommend EKS (strategic value)
  * Tier 3: EKS {tier2_pct}-{tier3_pct}% more → Recommend EC2 (mention EKS as alternative)
  * Tier 4: EKS >{tier3_pct}% more → Recommend EC2 only

**CRITICAL FOR COST ANALYSIS SECTION:**
- Show Option 3: Hybrid (EC2 + EKS) with above costs
- Use the PRE-CALCULATED recommendation above
- DO NOT recalculate or change the recommendation
- Highlight strategic benefits: containerization, scalability, modernization

**═══════════════════════════════════════════════════════════════**
"""
    else:
        # EKS disabled or not available
        eks_context = """

**═══════════════════════════════════════════════════════════════**
**EKS ANALYSIS: DISABLED**
**═══════════════════════════════════════════════════════════════**

**CRITICAL INSTRUCTIONS:**
- DO NOT mention EKS, Kubernetes, or containerization anywhere in the business case
- Show ONLY 2 pricing options (EC2 Instance SP and Compute SP)
- DO NOT include Option 3 (Hybrid)
- Focus on EC2-only migration strategy

**═══════════════════════════════════════════════════════════════**
"""
    
    # BUILD INFRASTRUCTURE DATA SECTION FROM PRE-COMPUTED SUMMARIES
    # This is the CRITICAL section that provides real VM data to the LLM
    infrastructure_data_section = ""
    if rvtools_summary:
        infrastructure_data_section = f"""

**═══════════════════════════════════════════════════════════════**
**PRE-COMPUTED RVTOOLS SUMMARY** (MANDATORY - Use these exact numbers)
**═══════════════════════════════════════════════════════════════**

These numbers were calculated directly from the RVTools file in Python.
USE ONLY THESE PRE-COMPUTED VALUES:

- **Total VMs for Migration**: {rvtools_summary['total_vms']}
- **Total vCPUs**: {rvtools_summary['total_vcpus']}
- **Total Memory (GB)**: {rvtools_summary['total_memory_gb']:.1f}
- **Total Storage (TB)**: {rvtools_summary['total_storage_tb']:.1f}
- **Windows VMs**: {rvtools_summary.get('windows_vms', 0)}
- **Linux VMs**: {rvtools_summary.get('linux_vms', 0)}

**CRITICAL**: Use ONLY these numbers in ALL sections (Current State, Executive Summary, etc.)

**═══════════════════════════════════════════════════════════════**
"""
    elif it_inventory_summary:
        infrastructure_data_section = f"""

**═══════════════════════════════════════════════════════════════**
**PRE-COMPUTED IT INVENTORY SUMMARY** (MANDATORY - Use these exact numbers)
**═══════════════════════════════════════════════════════════════**

These numbers were calculated directly from the IT Inventory file in Python.
USE ONLY THESE PRE-COMPUTED VALUES:

- **Total Servers for Migration**: {it_inventory_summary['total_vms']}
- **Total Databases (RDS)**: {it_inventory_summary['total_databases']}
- **Total vCPUs (Servers + Databases)**: {it_inventory_summary['total_vcpus']}
- **Total Memory GB (Servers + Databases)**: {it_inventory_summary['total_memory_gb']:.1f}
- **Total Storage TB (Servers + Databases)**: {it_inventory_summary['total_storage_tb']:.1f}
- **Windows Servers**: {it_inventory_summary['windows_vms']}
- **Linux Servers**: {it_inventory_summary['linux_vms']}

**CRITICAL**: Use ONLY these numbers in ALL sections (Current State, Executive Summary, etc.)

**═══════════════════════════════════════════════════════════════**
"""
    elif atx_summary:
        infrastructure_data_section = f"""

**═══════════════════════════════════════════════════════════════**
**ATX PPT PRE-COMPUTED SUMMARY** (MANDATORY - Use these exact numbers)
**═══════════════════════════════════════════════════════════════**

These numbers were extracted directly from the ATX PowerPoint presentation.
USE ONLY THESE PRE-EXTRACTED VALUES:

- **Total VMs for Migration**: {atx_summary['total_vms']}
- **Windows VMs**: {atx_summary['windows_vms']}
- **Linux VMs**: {atx_summary['linux_vms']}
- **Total Databases**: {atx_summary.get('database_count', 0)}
- **Total Storage**: {atx_summary['total_storage_tb']:.2f} TB
- **Monthly AWS Cost**: ${atx_summary['total_monthly']:,.2f}
- **Annual AWS Cost (ARR)**: ${atx_summary['total_arr']:,.2f}

**CRITICAL**: Use ONLY these numbers in ALL sections (Current State, Executive Summary, etc.)

**═══════════════════════════════════════════════════════════════**
"""
    
    assessments_note = f"\n**ASSESSMENTS ALREADY COMPLETED**: {', '.join(completed_assessments)}\n**DO NOT recommend these assessments again.**" if completed_assessments else ""
    
    # Get TCO configuration
    tco_enabled = TCO_COMPARISON_CONFIG.get('enable_tco_comparison', False)
    tco_note = f"\n**TCO_ENABLED**: {tco_enabled}\n**CRITICAL**: {'Include on-premises TCO comparison if AWS < On-Prem' if tco_enabled else 'DO NOT include on-premises costs, TCO comparison, or break-even analysis'}"
    
    # Build comprehensive context with actual analysis results
    context = f"""
{project_context}
{infrastructure_data_section}
{tco_note}
{eks_context}

**ANALYSIS RESULTS FROM PREVIOUS AGENTS:**

### Current State Analysis:
{get_result_text('current_state_analysis')}

### Migration Readiness Assessment (MRA):
{get_mra_content()}

### Cost Analysis:
{get_result_text('agent_aws_cost_arr')}

### Migration Strategy:
{get_result_text('agent_migration_strategy')}

### Migration Plan:
{get_result_text('agent_migration_plan')}
{assessments_note}

**CRITICAL INSTRUCTIONS:**
- Use ONLY the ACTUAL NUMBERS and data from the analysis results above
- Extract and use REAL values from the analysis - NOT placeholders like [total VM count] or [$X]
- Look for specific metrics in the analysis text and use those exact numbers
- Do NOT make up generic examples or use placeholder data
- IGNORE any example numbers you may have seen in prompts or previous responses
- Ensure all recommendations align with the project context and actual findings
- RESPECT the TCO_ENABLED flag above - if False, DO NOT include any on-premises cost calculations
- IF MRA analysis is available, incorporate MRA findings, gaps, and recommendations into relevant sections
"""
    
    # Generate each section
    # NOTE: cost_analysis section is NOT generated by LLM
    # It's created entirely by inject_pricing_comparison_table() post-processing
    section_configs = [
        ('executive_summary', EXECUTIVE_SUMMARY_PROMPT, 'Executive Summary'),
        ('current_state', CURRENT_STATE_PROMPT, 'Current State Analysis'),
        ('migration_strategy', MIGRATION_STRATEGY_PROMPT, 'Migration Strategy'),
        # ('cost_analysis', COST_ANALYSIS_PROMPT, 'Cost Analysis and TCO'),  # SKIPPED - handled by table injection
        ('migration_roadmap', MIGRATION_ROADMAP_PROMPT, 'Migration Roadmap'),
        ('benefits_risks', BENEFITS_RISKS_PROMPT, 'Benefits and Risks'),
        ('recommendations', RECOMMENDATIONS_PROMPT, 'Recommendations and Next Steps')
    ]
    
    for section_key, prompt, section_name in section_configs:
        print(f"\nGenerating: {section_name}...")
        try:
            agent = create_section_agent(prompt)
            
            # Build section-specific context to reduce token usage
            # Only include relevant agent results for each section
            if section_key == 'executive_summary':
                # Executive summary needs all results but condensed
                section_context = f"""
{project_context}
{infrastructure_data_section}
{tco_note}

**ANALYSIS SUMMARY (condensed for Executive Summary):**
- Current State: {get_result_text('current_state_analysis', 2000)}
- MRA Readiness: {get_mra_content()[:1500]}
- Costs: {get_result_text('agent_aws_cost_arr', 2000)}
- Strategy: {get_result_text('agent_migration_strategy', 1500)}
{assessments_note}
"""
            elif section_key == 'current_state':
                # Current state needs current state analysis AND MRA
                section_context = f"""
{project_context}
{infrastructure_data_section}

**CURRENT STATE ANALYSIS:**
{get_result_text('current_state_analysis', 4000)}

**MIGRATION READINESS ASSESSMENT (MRA):**
{get_mra_content()[:2000]}
"""
            elif section_key == 'migration_strategy':
                # Migration strategy needs strategy, current state, and MRA
                section_context = f"""
{project_context}
{infrastructure_data_section}

**CURRENT STATE:**
{get_result_text('current_state_analysis', 2000)}

**MRA READINESS:**
{get_mra_content()[:1500]}

**MIGRATION STRATEGY:**
{get_result_text('agent_migration_strategy', 4000)}
"""
            elif section_key == 'cost_analysis':
                # Cost analysis needs cost data AND EKS data
                section_context = f"""
{project_context}
{tco_note}
{eks_context}

**COST ANALYSIS:**
{get_result_text('agent_aws_cost_arr', 4000)}
"""
            elif section_key == 'migration_roadmap':
                # Migration roadmap needs plan, strategy, and MRA
                section_context = f"""
{project_context}

**MRA READINESS:**
{get_mra_content()[:1500]}

**MIGRATION STRATEGY:**
{get_result_text('agent_migration_strategy', 2000)}

**MIGRATION PLAN:**
{get_result_text('agent_migration_plan', 4000)}
{assessments_note}
"""
            elif section_key == 'benefits_risks':
                # Benefits and risks needs all context
                section_context = context
            elif section_key == 'recommendations':
                # Recommendations needs all key findings including MRA
                section_context = f"""
{project_context}
{assessments_note}

**KEY FINDINGS:**
- Current State: {get_result_text('current_state_analysis', 1500)}
- MRA Readiness: {get_mra_content()[:1500]}
- Costs: {get_result_text('agent_aws_cost_arr', 1500)}
- Strategy: {get_result_text('agent_migration_strategy', 1500)}
"""
            else:
                # Default: use full context
                section_context = context
            
            # For Executive Summary and Cost Analysis sections, inject exact costs from Excel
            if section_key in ['executive_summary', 'cost_analysis']:
                exact_costs = extract_exact_costs_from_excel(agent_results)
                if exact_costs:
                    print(f"✓ Injecting exact costs from Excel file into {section_name}")
                    task = f"{section_context}\n\n{exact_costs}\n\nGenerate the {section_name} section based on the available analysis."
                else:
                    print(f"⚠ Could not extract exact costs from Excel for {section_name}, using tool output only")
                    task = f"{section_context}\n\nGenerate the {section_name} section based on the available analysis."
            else:
                # Create task with section-specific context
                task = f"{section_context}\n\nGenerate the {section_name} section based on the available analysis."
            
            # Retry logic for transient Bedrock streaming errors
            import time as _section_time
            _section_max_retries = 2
            _section_result = None
            for _section_attempt in range(1, _section_max_retries + 1):
                try:
                    _section_result = agent(task)
                    break
                except Exception as _stream_err:
                    err_str = str(_stream_err).lower()
                    is_retryable = any(kw in err_str for kw in [
                        'stream', 'throttl', 'timeout', 'serviceunavailable',
                        'internalserver', 'modelstreamerror', 'connection'
                    ])
                    if is_retryable and _section_attempt < _section_max_retries:
                        wait = 2 ** _section_attempt * 5
                        print(f"  ⚠ {section_name} streaming error (attempt {_section_attempt}), retrying in {wait}s...")
                        _section_time.sleep(wait)
                    else:
                        raise
            result = _section_result
            
            # Extract text content from the result
            # result.message is a dict with 'role' and 'content' keys
            # content is a list of dicts with 'text' key
            if hasattr(result, 'message') and isinstance(result.message, dict):
                content_list = result.message.get('content', [])
                if content_list and isinstance(content_list, list):
                    content = content_list[0].get('text', '') if content_list else ''
                else:
                    content = str(result.message)
            else:
                content = str(result.message) if result.message else ""
            
            # Check if content was truncated
            if content and ("[Continued in next part...]" in content or content.endswith("...")):
                print(f"⚠️  {section_name} may be truncated - consider reducing detail or increasing max_tokens")
            
            sections[section_key] = content
            print(f"✓ {section_name} generated ({len(content)} chars)")
            
        except Exception as e:
            print(f"✗ Error generating {section_name}: {str(e)}")
            sections[section_key] = f"# {section_name}\n\n*Section generation failed: {str(e)}*"
    
    # Add empty placeholder for cost_analysis section
    # This will be populated entirely by inject_pricing_comparison_table()
    sections['cost_analysis'] = ""
    print("ℹ Cost Analysis section will be generated by table injection (not LLM)")
    
    # Combine all sections
    business_case = combine_sections(sections, project_context)
    
    # Post-process: Remove duplicate H1 Cost Analysis section (LLM-generated)
    # This prevents duplication with the H2 section where table is injected
    # NOTE: This is now redundant since we don't generate the section, but keeping for safety
    business_case = remove_duplicate_cost_analysis_section(business_case)
    
    # Post-process: Inject Pricing Comparison table directly into Cost Analysis section
    business_case = inject_pricing_comparison_table(business_case, agent_results)
    
    # Post-process: Inject Key Financial Metrics to match recommended option
    business_case = inject_key_financial_metrics(business_case, agent_results)
    
    # Post-process: Inject Current State Highlights with deterministic data
    business_case = inject_current_state_highlights(business_case, rvtools_summary, it_inventory_summary, atx_summary)
    
    return business_case

def combine_sections(sections, project_context):
    """Combine all sections into final business case document"""
    
    # Extract project info
    project_info = {}
    for line in project_context.split('\n'):
        if 'Project Name:' in line:
            project_info['name'] = line.split(':', 1)[1].strip()
        elif 'Customer Name:' in line:
            project_info['customer'] = line.split(':', 1)[1].strip()
        elif 'Target AWS Region:' in line:
            project_info['region'] = line.split(':', 1)[1].strip()
    
    # Build final document
    document = f"""# AWS Migration Business Case
## {project_info.get('customer', 'Customer')} - {project_info.get('name', 'Migration Project')}

**Target Region:** {project_info.get('region', 'N/A')}  
**Generated:** {datetime.now().strftime('%B %d, %Y %H:%M:%S')}

---

"""
    
    # Add table of contents (plain text, no links)
    document += """## Table of Contents

1. Executive Summary
2. Current State Analysis
3. Migration Strategy
4. Cost Analysis and TCO
5. Migration Roadmap
6. Benefits and Risks
7. Recommendations and Next Steps
8. Appendix: AWS Partner Programs for Migration and Modernization

---

"""
    
    # Add each section
    section_order = [
        ('executive_summary', 'Executive Summary'),
        ('current_state', 'Current State Analysis'),
        ('migration_strategy', 'Migration Strategy'),
        ('cost_analysis', 'Cost Analysis and TCO'),
        ('migration_roadmap', 'Migration Roadmap'),
        ('benefits_risks', 'Benefits and Risks'),
        ('recommendations', 'Recommendations and Next Steps')
    ]
    
    for section_key, section_title in section_order:
        content = sections.get(section_key, f'*{section_title} not available*')
        document += f"\n## {section_title}\n\n{content}\n\n---\n"
    
    # Add appendix with AWS partner programs
    document += f"\n{get_appendix()}\n\n"
    
    # Add footer
    document += f"""
## Document Information

**Generated by:** AWS Migration Business Case Generator  
**Generation Method:** Multi-Stage AI Analysis  
**Model:** {model_id_claude3_7}  
**Date:** {datetime.now().strftime('%B %d, %Y %H:%M:%S')}

---

*This business case was generated using AI-powered analysis of your infrastructure data, assessment reports, and migration readiness evaluation. All recommendations should be validated with AWS solutions architects and your technical teams.*
"""
    
    # Clean up markdown code fences
    document = cleanup_markdown_fences(document)
    
    return document


def cleanup_markdown_fences(text):
    """
    Remove markdown code fence markers (```markdown, ```, etc.) from the text
    These sometimes appear in LLM output and should be removed for cleaner presentation
    """
    import re
    
    # Remove ```markdown at the start of code blocks
    text = re.sub(r'```markdown\s*\n', '', text)
    
    # Remove ``` at the end of code blocks (but preserve code blocks that are intentional)
    # Only remove standalone ``` on its own line
    text = re.sub(r'\n```\s*\n', '\n\n', text)
    
    # Remove any remaining ``` that appear at start or end of lines
    text = re.sub(r'^```\s*$', '', text, flags=re.MULTILINE)
    
    return text

if __name__ == "__main__":
    print("Multi-stage business case generator module loaded")
    print(f"Using model: {model_id_claude3_7}")
    print(f"Max tokens per section: {MAX_TOKENS_BUSINESS_CASE}")
