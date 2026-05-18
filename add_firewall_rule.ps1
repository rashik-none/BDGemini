$firefox = "C:\Users\BBC\AppData\Local\invisible-playwright\invisible-playwright\Cache\firefox-1\firefox.exe"

Write-Host "Adding Windows Firewall exceptions for InvisiblePlaywright Firefox..."

netsh advfirewall firewall add rule `
    name="InvisiblePlaywright Firefox OUT" `
    dir=out `
    action=allow `
    program="$firefox" `
    enable=yes `
    profile=any

netsh advfirewall firewall add rule `
    name="InvisiblePlaywright Firefox IN" `
    dir=in `
    action=allow `
    program="$firefox" `
    enable=yes `
    profile=any

Write-Host ""
Write-Host "Done! Verifying rules..."
netsh advfirewall firewall show rule name="InvisiblePlaywright Firefox OUT"
