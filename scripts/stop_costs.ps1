<#
.SYNOPSIS
    Destroys cost-generating resources (AOSS + Bedrock KB) while preserving all other infra.

.DESCRIPTION
    Amazon OpenSearch Serverless (AOSS) is the only resource in this project billed
    continuously regardless of usage: minimum 2 OCU x $0.24/OCU/h = ~$11.52/day.

    This script runs targeted terraform destroy for:
      - aws_bedrockagent_data_source.kb
      - aws_bedrockagent_knowledge_base.main
      - aws_opensearchserverless_collection.kb
      - aws_opensearchserverless_access_policy.kb
      - aws_opensearchserverless_security_policy.kb_network
      - aws_opensearchserverless_security_policy.kb_encryption

    Everything else (Lambda, API GW, DynamoDB, S3, Cognito, CloudTrail) costs
    nothing when idle and is left intact.

.NOTES
    To restore: run `terraform apply` from the terraform/ directory.
    All state is preserved in S3 remote backend — re-apply recreates AOSS + KB.
#>

param(
    [switch]$Force   # skip confirmation prompt
)

$tf  = "C:\Users\Michal\AppData\Local\Microsoft\WinGet\Packages\Hashicorp.Terraform_Microsoft.Winget.Source_8wekyb3d8bbwe\terraform.exe"
$dir = "$PSScriptRoot\..\terraform"

# ── Pre-flight ────────────────────────────────────────────────────────────────

if (-not (Test-Path $tf)) {
    Write-Error "terraform.exe not found at: $tf"
    exit 1
}

if (-not (Test-Path $dir)) {
    Write-Error "Terraform directory not found: $dir"
    exit 1
}

Write-Host ""
Write-Host "=========================================" -ForegroundColor Yellow
Write-Host "  IAM Gateway – Stop Costs" -ForegroundColor Yellow
Write-Host "=========================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "Resources to DESTROY (cost drivers):" -ForegroundColor Red
Write-Host "  - aws_bedrockagent_data_source.kb"
Write-Host "  - aws_bedrockagent_knowledge_base.main"
Write-Host "  - aws_opensearchserverless_collection.kb"
Write-Host "  - aws_opensearchserverless_access_policy.kb"
Write-Host "  - aws_opensearchserverless_security_policy.kb_network"
Write-Host "  - aws_opensearchserverless_security_policy.kb_encryption"
Write-Host ""
Write-Host "Resources PRESERVED (idle = no cost):" -ForegroundColor Green
Write-Host "  - Lambda, API Gateway, DynamoDB, S3, Cognito, CloudTrail, IAM"
Write-Host ""
Write-Host "Estimated savings: ~`$11.52/day (~`$345/month)" -ForegroundColor Cyan
Write-Host "To restore: cd terraform; terraform apply" -ForegroundColor Cyan
Write-Host ""

if (-not $Force) {
    $confirm = Read-Host "Type YES to proceed with destroy"
    if ($confirm -ne "YES") {
        Write-Host "Aborted." -ForegroundColor Gray
        exit 0
    }
}

# ── Targeted destroy (dependency order: data source → KB → AOSS) ─────────────

$targets = @(
    "aws_bedrockagent_data_source.kb",
    "aws_bedrockagent_knowledge_base.main",
    "aws_opensearchserverless_collection.kb",
    "aws_opensearchserverless_access_policy.kb",
    "aws_opensearchserverless_security_policy.kb_network",
    "aws_opensearchserverless_security_policy.kb_encryption"
)

$targetArgs = $targets | ForEach-Object { "-target=$_" }

Push-Location $dir
try {
    Write-Host ""
    Write-Host "Running: terraform destroy $($targetArgs -join ' ')" -ForegroundColor DarkGray
    Write-Host ""
    & $tf destroy @targetArgs -auto-approve
    if ($LASTEXITCODE -ne 0) {
        Write-Error "terraform destroy failed (exit code $LASTEXITCODE)"
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "=========================================" -ForegroundColor Green
Write-Host "  Done. AOSS billing stopped." -ForegroundColor Green
Write-Host "  Restore anytime: cd terraform; terraform apply" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Green
Write-Host ""
