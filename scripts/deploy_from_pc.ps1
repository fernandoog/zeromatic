# Deploy contestador sources to a Raspberry Pi over SSH.
# Example:
#   .\scripts\deploy_from_pc.ps1 -HostName 192.168.1.23 -User asterisk -IdentityFile "$env:USERPROFILE\.ssh\id_ed25519"

param(
    [Parameter(Mandatory = $true)]
    [string]$HostName,

    [string]$User = "asterisk",

    [string]$IdentityFile = "",

    [string]$RemoteTmp = "/tmp/contestador-deploy"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$sshArgs = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=15")
if ($IdentityFile) {
    $sshArgs += @("-i", $IdentityFile)
}
$target = "${User}@${HostName}"

Write-Host "Packaging from $Root ..."
$tar = Join-Path $env:TEMP "contestador-deploy.tgz"
if (Test-Path $tar) { Remove-Item $tar -Force }

Push-Location $Root
try {
    tar -czf $tar `
        --exclude=.git `
        --exclude=media/*.wav `
        --exclude=__pycache__ `
        bin docs etc systemd requirements.txt README.md LICENSE media/.gitkeep
} finally {
    Pop-Location
}

Write-Host "Uploading to $target:$RemoteTmp ..."
ssh @sshArgs $target "rm -rf $RemoteTmp && mkdir -p $RemoteTmp"
scp @sshArgs $tar "${target}:/tmp/contestador-deploy.tgz"
ssh @sshArgs $target "tar -xzf /tmp/contestador-deploy.tgz -C $RemoteTmp && sudo bash $RemoteTmp/bin/install_on_pi.sh $RemoteTmp"

Write-Host "Done. Check: ssh $target 'systemctl is-active contestador'"
