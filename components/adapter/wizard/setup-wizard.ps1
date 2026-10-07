param([switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
# All user paths travel as JSON on stdin. No user value becomes command text.
$packageRoot = $PSScriptRoot
$pythonPath = Join-Path $packageRoot 'python\python.exe'
$backendPath = Join-Path $packageRoot 'app\skate_wizard.py'
$links = @{
    loader = 'https://github.com/UE4SS-RE/RE-UE4SS/releases/download/experimental-latest/UE4SS_v3.0.1-1152-ge3ba1016.zip'
    converter = 'https://github.com/chasmlol/2010-rust-rewrite-mashup/releases/download/v0.4.0/2010-Rust-Rewrite-Mashup-windows-x64.zip'
    extractor = 'https://github.com/antangelo/xdvdfs/releases/download/v0.8.3/xdvdfs-windows-1cc850bf1b3487fad7ec7c9eed01d83e8fc75ba4.zip'
}
if ($ValidateOnly) { 'Wizard syntax loaded; no UI or operation started.'; return }
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Dragonwilds Skate - setup'
$form.Size = New-Object System.Drawing.Size(930, 800)
$form.MinimumSize = New-Object System.Drawing.Size(880, 740)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$outer = New-Object System.Windows.Forms.TableLayoutPanel
$outer.Dock = 'Fill'; $outer.Padding = New-Object System.Windows.Forms.Padding(18)
$outer.ColumnCount = 1; $outer.RowCount = 5
@('Absolute,64','Absolute,56','Percent,100','Absolute,65','Absolute,155') | ForEach-Object {
    $kind,$height = $_.Split(',')
    [void]$outer.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::$kind, [float]$height)))
}
$form.Controls.Add($outer)
$title = New-Object System.Windows.Forms.Label
$title.Dock = 'Fill'; $title.Text = "Dragonwilds Skate`r`nUse your own game files and solo saves. Start with step 1; prepared data stays private."
$outer.Controls.Add($title,0,0)
$gameRow = New-Object System.Windows.Forms.FlowLayoutPanel
$gameRow.Dock='Fill'; $gameRow.WrapContents=$false
$gameLabel=New-Object System.Windows.Forms.Label; $gameLabel.Text='Dragonwilds folder';$gameLabel.AutoSize=$true;$gameLabel.Margin=New-Object System.Windows.Forms.Padding(0,8,8,0)
$gameText=New-Object System.Windows.Forms.TextBox;$gameText.Width=530
$gameBrowse=New-Object System.Windows.Forms.Button;$gameBrowse.Text='Browse...';$gameBrowse.AutoSize=$true
$gameRow.Controls.AddRange(@($gameLabel,$gameText,$gameBrowse));$outer.Controls.Add($gameRow,0,1)
$tabs=New-Object System.Windows.Forms.TabControl;$tabs.Dock='Fill';$outer.Controls.Add($tabs,0,2)
$progressPanel=New-Object System.Windows.Forms.Panel;$progressPanel.Dock='Fill'
$progressLabel=New-Object System.Windows.Forms.Label;$progressLabel.Dock='Fill';$progressLabel.Text='Ready when you are.';$progressLabel.Padding=New-Object System.Windows.Forms.Padding(0,4,0,0)
$progress=New-Object System.Windows.Forms.ProgressBar;$progress.Dock='Bottom';$progress.Height=18;$progress.Maximum=1000;$progress.Style='Blocks'
$progressPanel.Controls.Add($progressLabel);$progressPanel.Controls.Add($progress);$outer.Controls.Add($progressPanel,0,3)
$log=New-Object System.Windows.Forms.TextBox;$log.Dock='Fill';$log.Multiline=$true;$log.ReadOnly=$true;$log.ScrollBars='Vertical';$log.WordWrap=$true
$log.Text='Select RSDragonwilds in Steam > Manage > Browse local files, then browse to that folder here.'
$logPanel=New-Object System.Windows.Forms.Panel;$logPanel.Dock='Fill'
$details=New-Object System.Windows.Forms.Button;$details.Dock='Bottom';$details.Height=28;$details.Text='View technical details'
$script:technical='No completed operation yet.'
$details.Add_Click({
    $dialog=New-Object System.Windows.Forms.Form;$dialog.Text='Setup details (may contain private paths)';$dialog.Size=New-Object System.Drawing.Size(780,520);$dialog.StartPosition='CenterParent'
    $text=New-Object System.Windows.Forms.TextBox;$text.Dock='Fill';$text.Multiline=$true;$text.ReadOnly=$true;$text.ScrollBars='Both';$text.WordWrap=$false;$text.Text=$script:technical
    $dialog.Controls.Add($text);[void]$dialog.ShowDialog($form);$dialog.Dispose()
})
$logPanel.Controls.Add($log);$logPanel.Controls.Add($details);$outer.Controls.Add($logPanel,0,4)
$script:running=$null; $script:stdoutTask=$null; $script:stderrTask=$null; $script:started=$null
$script:gotResult=$false; $script:buttons=New-Object System.Collections.Generic.List[System.Windows.Forms.Control]
$script:phaseText='';$script:actionClock=$null;$script:phaseClock=$null;$script:phaseKey=''

function Get-ElapsedText([double]$seconds) {
    $whole=[long][Math]::Max(0,[Math]::Floor($seconds))
    $hours=[long][Math]::Floor($whole/3600);$minutes=[long][Math]::Floor(($whole%3600)/60)
    if ($hours -gt 0) {return ('{0}h {1:00}m {2:00}s' -f $hours,$minutes,($whole%60))}
    return ('{0}m {1:00}s' -f $minutes,($whole%60))
}
function Set-ProgressState($data) {
    if ($script:gotResult) {return} # A log line can never replace the final result.
    if ($data.phase -and $data.label) {
        $key=([string]$data.operation)+'/'+([string]$data.phase)
        if ($script:phaseKey -ne $key) {
            $script:phaseKey=$key;$script:phaseClock=[System.Diagnostics.Stopwatch]::StartNew()
        }
        $script:phaseText=[string]$data.message
        if (!$script:phaseText) {$script:phaseText=[string]$data.label}
        $complete=$data.completed;$total=$data.total
        $valid=($complete -is [int] -or $complete -is [long]) -and ($total -is [int] -or $total -is [long])
        if ($valid -and $total -gt 0 -and $complete -ge 0 -and $complete -le $total) {
            $value=[int][Math]::Floor(1000.0*$complete/$total)
            if ($complete -lt $total) {$value=[Math]::Min(999,$value)}
            $progress.Style='Continuous';$progress.Value=$value
        } else {$progress.Style='Marquee';$progress.Value=0}
        if ($data.changed -ne $false) {Add-Log $script:phaseText}
    } elseif ($data.message) {Add-Log ([string]$data.message)}
}

function Add-Log([string]$text) {
    if ($log.TextLength -gt 100000) { $log.Text=$log.Text.Substring($log.TextLength-60000) }
    $log.AppendText("`r`n"+$text);$log.SelectionStart=$log.TextLength;$log.ScrollToCaret()
}
function Browse-Folder($target) {
    $dialog=New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description='Choose the requested folder';$dialog.ShowNewFolderButton=$false
    if (Test-Path -LiteralPath $target.Text -PathType Container) {$dialog.SelectedPath=$target.Text}
    if ($dialog.ShowDialog($form) -eq 'OK') {$target.Text=$dialog.SelectedPath}
    $dialog.Dispose()
}
function Browse-File($target,[string]$filter) {
    $dialog=New-Object System.Windows.Forms.OpenFileDialog;$dialog.Filter=$filter;$dialog.CheckFileExists=$true
    if ($dialog.ShowDialog($form) -eq 'OK') {$target.Text=$dialog.FileName};$dialog.Dispose()
}
$gameBrowse.Add_Click({Browse-Folder $gameText})
function Page([string]$text) {
    $page=New-Object System.Windows.Forms.TabPage;$page.Text=$text
    $flow=New-Object System.Windows.Forms.FlowLayoutPanel;$flow.Dock='Fill';$flow.AutoScroll=$true;$flow.FlowDirection='TopDown';$flow.WrapContents=$false
    $flow.Padding=New-Object System.Windows.Forms.Padding(12);$page.Controls.Add($flow);$tabs.TabPages.Add($page);return $flow
}
function Note($page,[string]$text) {
    $label=New-Object System.Windows.Forms.Label;$label.AutoSize=$true;$label.MaximumSize=New-Object System.Drawing.Size(790,0)
    $label.Text=$text;$label.Margin=New-Object System.Windows.Forms.Padding(0,4,0,10);$page.Controls.Add($label)
}
function Path-Row($page,[string]$caption,[string]$filter) {
    Note $page $caption
    $row=New-Object System.Windows.Forms.FlowLayoutPanel;$row.Width=800;$row.Height=38;$row.WrapContents=$false
    $text=New-Object System.Windows.Forms.TextBox;$text.Width=660
    $button=New-Object System.Windows.Forms.Button;$button.Text='Browse...';$button.AutoSize=$true
    if ($filter -eq 'folder') {$button.Add_Click({Browse-Folder $text}.GetNewClosure())}
    else {$button.Add_Click({Browse-File $text $filter}.GetNewClosure())}
    $row.Controls.AddRange(@($text,$button));$page.Controls.Add($row);return $text
}
function Button($page,[string]$text,[scriptblock]$handler) {
    $button=New-Object System.Windows.Forms.Button;$button.Text=$text;$button.AutoSize=$true;$button.MinimumSize=New-Object System.Drawing.Size(220,32)
    $button.Add_Click($handler);$page.Controls.Add($button);$script:buttons.Add($button)
}
function Link($page,[string]$text,[string]$url) {
    $link=New-Object System.Windows.Forms.LinkLabel;$link.Text=$text;$link.AutoSize=$true;$link.Margin=New-Object System.Windows.Forms.Padding(0,3,0,7)
    $link.Add_LinkClicked({[System.Diagnostics.Process]::Start($url)|Out-Null}.GetNewClosure());$page.Controls.Add($link)
}
function Start-Action([string]$action,[hashtable]$extra=@{}) {
    if ($script:running) {return}
    if ([string]::IsNullOrWhiteSpace($gameText.Text)) {Add-Log 'Choose your Dragonwilds folder first.';return}
    if ($action -in @('uninstall','archive-map','refresh-probe','recover')) {
        $explain = switch ($action) {
            'uninstall' {'Remove matching mod-owned files? Saves, converted assets, private caches, changed files and shared UE4SS files are preserved.'}
            'archive-map' {'Preserve the current owned map generation in a backup folder? You will need to prepare the map again. This does not erase saves.'}
            'refresh-probe' {'Preserve the old setup capture and request a new one? Close the game first; launch it again afterwards.'}
            'recover' {'Roll back an interrupted owned installation transaction? Independently changed files are preserved.'}
        }
        if ([System.Windows.Forms.MessageBox]::Show($form,$explain,'Confirm setup operation','OKCancel','Information') -ne 'OK') {return}
    }
    try {
        if (!(Test-Path -LiteralPath $pythonPath -PathType Leaf) -or !(Test-Path -LiteralPath $backendPath -PathType Leaf)) {throw 'Extract the complete release ZIP first.'}
        $request=@{action=$action;game=$gameText.Text};foreach($key in $extra.Keys){$request[$key]=$extra[$key]}
        $info=New-Object System.Diagnostics.ProcessStartInfo
        $info.FileName=$pythonPath;$info.Arguments='-B -u "'+$backendPath+'"';$info.WorkingDirectory=$packageRoot
        $info.UseShellExecute=$false;$info.CreateNoWindow=$true
        $info.RedirectStandardInput=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
        $info.StandardOutputEncoding=New-Object System.Text.UTF8Encoding($false)
        $info.StandardErrorEncoding=New-Object System.Text.UTF8Encoding($false)
        $process=New-Object System.Diagnostics.Process;$process.StartInfo=$info
        if (!$process.Start()) {throw 'Could not start bundled setup.'}
        $script:running=$process;$script:started=[DateTime]::UtcNow;$script:gotResult=$false
        $script:actionClock=[System.Diagnostics.Stopwatch]::StartNew()
        $script:phaseClock=[System.Diagnostics.Stopwatch]::StartNew();$script:phaseKey=''
        $script:phaseText='Checking files for '+$action.Replace('-',' ')
        $script:stderrTask=$process.StandardError.ReadToEndAsync()
        $script:stdoutTask=$process.StandardOutput.ReadLineAsync()
        # Windows PowerShell's .NET Framework lacks StandardInputEncoding.
        # Write UTF-8 bytes directly so relocated Unicode paths remain exact.
        $requestBytes=[System.Text.Encoding]::UTF8.GetBytes(($request|ConvertTo-Json -Compress))
        $process.StandardInput.BaseStream.Write($requestBytes,0,$requestBytes.Length)
        $process.StandardInput.BaseStream.Flush();$process.StandardInput.Close()
        foreach($button in $script:buttons){$button.Enabled=$false};$gameBrowse.Enabled=$false;$gameText.Enabled=$false
        $progress.Style='Marquee';$progress.Value=0
        $progressLabel.Text=$script:phaseText+"`r`nElapsed: 0m 00s"
        Add-Log ('Starting '+$action+'. Keep setup open; you can minimize this window.')
    } catch {Add-Log ('Cannot start: '+$_.Exception.Message)}
}
$p1=Page '1  Install'
Note $p1 'Close Dragonwilds and its mod helpers before installation or an update. The checked installer preserves unrelated mods and refuses incompatible shared loaders.'
Button $p1 'Check game and prerequisites' {Start-Action 'check'}
Link $p1 'Download the exact supported UE4SS ZIP (separate official prerequisite)' $links.loader
$loaderZip=Path-Row $p1 'Select that downloaded UE4SS ZIP. Setup checks the three exact required file hashes.' 'ZIP archive (*.zip)|*.zip'
Button $p1 'Import UE4SS from selected ZIP' {Start-Action 'import-loader' @{loader_zip=$loaderZip.Text}}
Button $p1 'Install / update mod' {Start-Action 'install'}
Note $p1 'A different UE4SS version is never overwritten. An enabled older DragonwildsSkateProbe must be disabled using its original manager first. Keep this extracted release folder for uninstall.'
$p2=Page '2  Your Skate 3 files'
Note $p2 'Select your own Xbox 360 Skate 3 .iso. You do not need to extract it or find any files inside it.'
$iso=Path-Row $p2 'Your Skate 3 ISO' 'Xbox 360 disc image (*.iso)|*.iso'
Note $p2 'Prepare my ISO downloads and verifies the official extraction and conversion tools, then runs them locally. The ISO is preserved and never uploaded. An internet connection is needed for the first tool download.'
Button $p2 'Prepare my ISO' {Start-Action 'convert' @{source=$iso.Text;confirm_tools=$true}}
Note $p2 'Wait for preparation to finish, then continue to step 3. Keep setup open; you may minimize it. No game files are bundled with the mod.'
Note $p2 'Already prepared your files, or need offline tool ZIPs? Use the Advanced files tab. PS3 images and the newer PC skate. are not supported.'
$p3=Page '3  Prepare and play'
Note $p3 'First launch: enter your own solo world and wait for the setup capture message. Then close Dragonwilds. The next steps read your installed game data and prepare permanent map collision.'
Button $p3 'Launch game to capture settings' {Start-Action 'capture'}
Button $p3 'Prepare / resume map' {Start-Action 'prepare-map'}
Note $p3 'Map preparation can take a long time. Repeating this button resumes only identical verified inputs; incompatible old data is preserved. Progress logs are in DragonwildsSkateData\map-data\setup-logs.'
Button $p3 'Prepare building catalogue' {Start-Action 'prepare-buildings'}
Button $p3 'Verify readiness' {Start-Action 'verify'}
Button $p3 'Play Dragonwilds' {Start-Action 'play'}
Note $p3 'Each solo save gets its own building observations automatically. No developer saves or collision snapshots are imported. Equip Skateboard in Mounts, close the menu, and use your normal mount control.'
$p4=Page 'Repair / uninstall'
Note $p4 'Stop the helper, then close Dragonwilds before maintenance. These actions preserve your game saves and private converted assets.'
Button $p4 'Stop mod helper' {Start-Action 'stop'}
Button $p4 'Refresh first-run capture' {Start-Action 'refresh-probe'}
Button $p4 'Archive map for a game update' {Start-Action 'archive-map'}
Button $p4 'Recover interrupted installation' {Start-Action 'recover'}
Button $p4 'Uninstall owned mod files' {Start-Action 'uninstall'}
Note $p4 'Run uninstall from this extracted release, outside the installed DragonwildsSkate folder. Existing shared UE4SS files, configuration, modified files and private caches stay in place.'
$advanced=Page 'Advanced files'
Note $advanced 'Optional alternatives. The normal route only needs your ISO in step 2.'
$assets=Path-Row $advanced 'Previously converted assets folder' 'folder'
Button $advanced 'Import prepared assets / resume copy' {Start-Action 'import-assets' @{source=$assets.Text}}
$source=Path-Row $advanced 'ISO or extracted default.xex (keep data beside default.xex)' 'Skate 3 input (*.iso;default.xex)|*.iso;default.xex'
Note $advanced 'Leave tool ZIPs blank for automatic downloads. For offline preparation, obtain the exact linked ZIPs on a connected computer and select them below.'
Link $advanced 'Official converter ZIP (v0.4.0)' $links.converter
$converter=Path-Row $advanced 'Optional converter ZIP' 'ZIP archive (*.zip)|*.zip'
Link $advanced 'Official ISO extractor ZIP' $links.extractor
$extractor=Path-Row $advanced 'Optional extractor ZIP (ISO only)' 'ZIP archive (*.zip)|*.zip'
Button $advanced 'Prepare advanced input' {Start-Action 'convert' @{source=$source.Text;converter_zip=$converter.Text;extractor_zip=$extractor.Text;confirm_tools=$true}}
Note $advanced 'Conversion attempts remain in DragonwildsSkateData\conversion. Each retry creates a new folder; earlier attempts are preserved.'
$timer=New-Object System.Windows.Forms.Timer;$timer.Interval=100
$timer.Add_Tick({
    if (!$script:running) {return}
    try {
        $elapsed=Get-ElapsedText $script:actionClock.Elapsed.TotalSeconds
        $form.Text='Dragonwilds Skate - working ('+$elapsed+')'
        $stageElapsed=Get-ElapsedText $script:phaseClock.Elapsed.TotalSeconds
        $progressLabel.Text=$script:phaseText+"`r`nElapsed: "+$elapsed+' | Stage: '+$stageElapsed
        for ($i=0;$i -lt 50 -and $script:stdoutTask -and $script:stdoutTask.IsCompleted;$i++) {
            $line=$script:stdoutTask.GetAwaiter().GetResult()
            if ($null -eq $line) {$script:stdoutTask=$null;break}
            try {
                $data=$line|ConvertFrom-Json
                if ($data.event -eq 'progress') {Set-ProgressState $data}
                elseif ($data.PSObject.Properties.Name -contains 'ok') {
                    $script:gotResult=$true
                    $script:technical=$data|ConvertTo-Json -Depth 12
                    if ($data.ok) {
                        $script:phaseText='Completed';$progress.Style='Continuous';$progress.Value=1000
                        if ($data.result.status) {Add-Log $data.result.status} else {Add-Log 'Operation completed.'}
                        if ($data.result.next) {Add-Log $data.result.next}
                    } else {
                        $script:phaseText='Action needed';$progress.Style='Blocks';$progress.Value=0
                        $friendly=([string]$data.error).Trim().Split("`n")[-1]
                        Add-Log ('Action needed: '+$friendly);Add-Log $data.next
                    }
                } else {Add-Log $line}
            } catch {Add-Log $line}
            $script:stdoutTask=$script:running.StandardOutput.ReadLineAsync()
        }
        if ($script:running.HasExited -and !$script:stdoutTask -and $script:stderrTask.IsCompleted) {
            $errors=$script:stderrTask.GetAwaiter().GetResult();if ($errors) {Add-Log $errors}
            if (!$script:gotResult) {
                $script:phaseText='Setup ended before a result';$progress.Style='Blocks';$progress.Value=0
                Add-Log ('Setup ended before a result. Exit code: '+$script:running.ExitCode+'. Retry the same step; retained preparation files will be checked.')
            }
            $script:actionClock.Stop();$script:phaseClock.Stop()
            $progressLabel.Text=$script:phaseText+"`r`nElapsed: "+$elapsed
            $script:running.Dispose();$script:running=$null;$script:stderrTask=$null
            $form.Text='Dragonwilds Skate - setup'
            foreach($button in $script:buttons){$button.Enabled=$true};$gameBrowse.Enabled=$true;$gameText.Enabled=$true
        }
    } catch {Add-Log ('Status error: '+$_.Exception.Message)}
})
$form.Add_FormClosing({
    if ($script:running -and !$script:running.HasExited) {
        $_.Cancel=$true
        [System.Windows.Forms.MessageBox]::Show($form,'An operation is running. Leave setup open until it finishes; you may minimize it. This protects resumable transactions.','Setup is busy','OK','Information')|Out-Null
    }
})
$timer.Start();[void]$form.ShowDialog();$timer.Stop();$timer.Dispose();$form.Dispose()
