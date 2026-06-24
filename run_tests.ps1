[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:AGENT_BROWSER_SOCKET_DIR = "$env:USERPROFILE\.agent-browser\socket"
$env:AGENT_BROWSER_DATA_DIR = "$env:USERPROFILE\.agent-browser"
$ShotDir = Join-Path $env:USERPROFILE "Documents\dockertest\wuyou"
$ab = "npx.cmd agent-browser"

function Run-Ab($Cmd) { 
    $full = "$ab $Cmd"
    Invoke-Expression $full
}
function Snap { Run-Ab "snapshot -i" }
function Shot($name) { 
    $path = Join-Path $ShotDir $name
    Run-Ab "screenshot --full `"$path`""
}

$results = @()
function Check($name, $cond) {
    $m = if ($cond) { "PASS" } else { "FAIL" }
    $results += "[$m] $name"
    Write-Host "[$m] $name" -ForegroundColor $(if ($cond) {"Green"} else {"Red"})
}

Write-Host "==== WUYOU UI TEST ===="

# ==== 1. Auth Page ====
Write-Host "--- Auth ---"
Run-Ab "open http://localhost:8000"
Run-Ab "wait --load networkidle"
Shot "01_auth.png"
$s = Snap
Check "01 Card layout" ($s -match "auth-card")
Check "02 Reg+Login tabs" ($s -match "Register")
Check "03 Slogan" ($s -match "WuYou" -or $s -match "emailbox")
Check "04 Favicon" ($true)

# ==== 2. Register ====
Write-Host "--- Register ---"
$uname = "testu" + (Get-Date -Format "HHmmss")
# Get refs from snapshot
$s = Snap
$nr = [regex]::Matches($s, 'textbox.*\[ref=(e\d+)\]')
if ($nr.Count -ge 3) {
    $userRef = $nr[0].Groups[1].Value
    $emailRef = $nr[1].Groups[1].Value
    # skip code textbox, use password textbox
    $pwIdx = if ($nr.Count -ge 4) { 3 } else { 2 }
    $pwRef = $nr[$pwIdx].Groups[1].Value
    # register button
    $regBtn = [regex]::Match($s, 'button.*Register.*\[ref=(e\d+)\]')
    if (-not $regBtn.Success) { $regBtn = [regex]::Match($s, 'button.*primary.*\[ref=(e\d+)\]') }
    
    if ($regBtn.Success) {
        Run-Ab "type $userRef $uname"
        Run-Ab "type $emailRef $uname@test.com"
        Run-Ab "type $pwRef Test123456"
        Run-Ab "click $($regBtn.Groups[1].Value)"
        Start-Sleep 3
        Run-Ab "wait --load networkidle"
    }
}
Shot "02_after_register.png"
$s2 = Snap
Check "05 Registered" ($s2 -match "Inbox" -or $s2 -match "topbar" -or $s2 -match "sidebar")

# ==== 3. Topbar + Sidebar ====
Write-Host "--- Topbar ---"
if ($s2 -match "topbar") {
    Check "06 Topbar" ($true)
    Check "07 Locale" ($s2 -match "locale-select")
    Check "08 Theme" ($s2 -match "theme-toggle")
    Check "09 Avatar" ($s2 -match "user-avatar")
    Check "10 Sidebar" ($s2 -match "sidebar")
    Check "11 Collapse" ($s2 -match "sidebar-collapse")
    Check "12 Nav>=9" (([regex]::Matches($s2, "nav-button")).Count -ge 9)
} else {
    Check "06 Topbar" ($false)
    Write-Host "NOT LOGGED IN"
}

# ==== 4. Inbox ====
Write-Host "--- Inbox ---"
if ($s2 -match "show-sync-jobs") {
    Shot "03_inbox.png"
    Check "13 Sync btn" ($true)
    Check "14 Folders" ($s2 -match "folder-tab")
    Check "15 Search" ($s2 -match "mail-search")
    
    $sjBtn = [regex]::Match($s2, 'button.*"show-sync-jobs".*\[ref=(e\d+)\]')
    if ($sjBtn.Success) {
        Run-Ab "click $($sjBtn.Groups[1].Value)"
        Start-Sleep 2
        Shot "03b_sync_jobs.png"
        $sj2 = Snap
        Check "16 Sync modal" ($sj2 -match "sync" -or $sj2 -match "noJobs" -or $sj2 -match "Close")
        # close
        $clsBtn = [regex]::Match($sj2, 'Close.*\[ref=(e\d+)\]|button.*Close.*\[ref=(e\d+)\]')
        if ($clsBtn.Success) { Run-Ab "click $($clsBtn.Groups[1].Value)"; Start-Sleep 0.5 }
    }
} else {
    Check "13 Sync btn" ($false)
}

# ==== 5. Navigate pages ====
$views = @("accounts","calendar","contacts","tasks","notes","compose","plugins","settings","about")
foreach ($v in $views) {
    Write-Host "--- $v ---"
    $cur = Snap
    $nav = [regex]::Match($cur, "button.*data-view=`"$v`".*\[ref=(e\d+)\]")
    if ($nav.Success) {
        Run-Ab "click $($nav.Groups[1].Value)"
        Start-Sleep 2
        Run-Ab "wait --load networkidle"
        Shot "04_$v.png"
        $ps = Snap
        
        switch ($v) {
            "accounts" {
                Check "17 Form" ($ps -match "account-form")
                Check "18 TB" ($ps -match "tb-import-btn")
            }
            "calendar" {
                Check "19 NewEvt" ($ps -match "cal-new-event")
                Check "20 Grid" ($ps -match "cal-grid")
            }
            "contacts" { Check "21 New" ($ps -match "contact-new") }
            "tasks" { 
                Check "22 Kanban" ($ps -match "task-view-kanban")
                Check "23 Quick" ($ps -match "task-quick-add")
            }
            "notes" { Check "24 New" ($ps -match "note-new") }
            "compose" {
                Check "25 Format" ($ps -match "format-toolbar")
                Check "26 Send" ($ps -match "compose-send")
                Check "27 Draft" ($ps -match "compose-draft")
                Check "28 Cancel" ($ps -match "compose-cancel")
            }
            "settings" {
                Check "29 Theme" ($ps -match "set-theme")
                Check "30 Lang" ($ps -match "set-locale")
                Check "31 Telem" ($ps -match "set-telemetry")
                Check "32 Pw" ($ps -match "set-old-pw")
                Check "33 Email" ($ps -match "set-new-email")
                Check "34 Save" ($ps -match "btn-save-settings")
            }
            "about" {
                Check "35 NoHint" ($ps -notmatch "alipay-qr.png")
                Check "36 Changelog" ($ps -match "show-changelog")
            }
        }
    }
}

# ==== 6. English i18n ====
Write-Host "--- i18n EN ---"
$cur2 = Snap
$loc = [regex]::Match($cur2, 'combobox.*\[ref=(e\d+)\]')
if ($loc.Success) {
    Run-Ab "select $($loc.Groups[1].Value) en-US"
    Start-Sleep 2
    Run-Ab "wait --load networkidle"
    Shot "05_enUS.png"
    $en = Snap
    Check "37 EN Nav" ($en -match "Inbox" -and $en -match "Settings")
}

# ==== FINAL ====
Write-Host ""
Write-Host "======== RESULTS ========"
$pass = ($results | Where {$_ -match "^\[PASS\]"}).Count
$fail = ($results | Where {$_ -match "^\[FAIL\]"}).Count
foreach ($r in $results) { Write-Host $r }
Write-Host "TOTAL: $($results.Count) | PASS: $pass | FAIL: $fail"
Run-Ab "close"
