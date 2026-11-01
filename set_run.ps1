<#
  启动项小工具  (HKCU\Software\Microsoft\Windows\CurrentVersion\Run)

  命令：
    copy    [名字]   生成单行命令并【复制到剪贴板】（默认）。不检查文件是否存在，
                     可对任意名字凭空生成，例如 copy SvcMonitor
    write   [名字]   写入 Run 键（真正的登录自启）。要求文件真实存在
    delete  [名字]   删除 Run 项（会二次确认）
    list            列出 Cache 目录里的 exe（含隐藏）与 Run 现有项
    help            显示帮助

  参数：
    -User 用户名     指定 C:\Users\<用户名>，用于目标机不是本机的情况
    -Path 目录       直接指定 exe 所在目录，跳过用户名询问

  用户名与大小写：
    - 只给 exe 名字时会询问用户名，直接回车 = 当前登录用户
    - 本机存在的目录会还原成磁盘上的真实大小写
    - 本机不存在的目录（多为操作远端目标机）按你输入的大小写原样保留
    - Windows 文件系统本身不区分大小写，cache 与 Cache 是同一个目录

  例子：
    .\set_run.ps1                                  等价 copy，交互选择后复制
    .\set_run.ps1 copy SvcMonitor                  凭空生成 Cache\SvcMonitor.exe 的命令
    .\set_run.ps1 copy LocaleMgr                   复制真实文件的启动命令
    .\set_run.ps1 copy LocaleMgr -User WDKRemoteUser
    .\set_run.ps1 copy LocaleMgr -Path "C:\Users\X\AppData\Local\Cache"
    .\set_run.ps1 write LocaleMgr                 直接写入（必须存在）
    .\set_run.ps1 delete LocaleMgr                直接删除
    .\set_run.ps1 list
    .\set_run.ps1 help

  说明：候选列表用 -Force 枚举，【包含隐藏文件】。
#>
param(
    [Parameter(Position = 0)][string]$Command,
    [Parameter(Position = 1)][string]$Name,
    [string]$User,
    [string]$Path
)

$ErrorActionPreference = 'Stop'

$RunKey   = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$CurrentUser = $env:USERNAME

# 列出 C:\Users 下的真实用户目录
function Get-UserProfiles {
    $skip = @('Public','Default','Default User','All Users','DefaultAppPool',
              'Classic .NET AppPool','LocalService','NetworkService')
    $out = @()
    foreach ($d in (Get-ChildItem 'C:\Users' -Directory -Force -ErrorAction SilentlyContinue)) {
        if ($d.Name.StartsWith('.')) { continue }
        if ($skip -contains $d.Name) { continue }
        if ($d.Name -match '\$') { continue }
        if (-not (Test-Path -LiteralPath (Join-Path $d.FullName 'NTUSER.DAT'))) { continue }
        $out += $d.Name
    }
    return $out
}

# 取路径在磁盘上的真实大小写；不存在则原样返回
function Get-RealPathCase {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return $Path }

    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($full)
    if ([string]::IsNullOrEmpty($root)) { return $Path }

    $cur = $root
    $rest = $full.Substring($root.Length).Trim('\', '/')
    foreach ($seg in ($rest -split '[\\/]')) {
        if ([string]::IsNullOrEmpty($seg)) { continue }
        $hit = $null
        foreach ($d in (Get-ChildItem -LiteralPath $cur -Force -ErrorAction SilentlyContinue)) {
            if ($d.Name -ieq $seg) { $hit = $d.Name; break }
        }
        if ($null -eq $hit) { return $Path }   # 某段不存在，放弃还原
        $cur = Join-Path $cur $hit
    }
    return $cur
}

# 按用户名求 Cache 目录
#   - 本地存在: 还原成磁盘真实大小写
#   - 本地不存在(多为远端目标机): 按输入原样保留大小写
function Get-UserCacheDir {
    param([string]$UserName, [string]$Override)

    if (-not [string]::IsNullOrWhiteSpace($Override)) {
        return (Get-RealPathCase -Path $Override)
    }

    if ([string]::IsNullOrWhiteSpace($UserName) -or $UserName -ieq $CurrentUser) {
        $local = $env:LOCALAPPDATA
    } else {
        $local = Join-Path (Join-Path 'C:\Users' $UserName) 'AppData\Local'
    }

    # 先按用户输入的大小写尝试（Windows 本身不区分，但保持显示正确）
    foreach ($name in @('Cache', 'cache')) {
        $cand = Join-Path $local $name
        if (Test-Path -LiteralPath $cand) { return (Get-RealPathCase -Path $cand) }
    }

    # 本地没有：说明是远端目标机路径，按输入原样生成（不改大小写）
    return (Join-Path $local 'Cache')
}

# 交互询问用户名：回车 = 当前用户
function Ask-User {
    param([string]$Default)

    if ([string]::IsNullOrWhiteSpace($Default)) { $Default = $CurrentUser }

    $profiles = @(Get-UserProfiles)
    if ($profiles.Count -gt 0) {
        Write-Host ('   本机用户: ' + ($profiles -join ', ')) -ForegroundColor DarkGray
    }
    Write-Host '   目标机不是本机时，直接输入目标机的用户名即可（大小写按目标机）' -ForegroundColor DarkGray
    $ans = Read-Host ('   用户名 [' + $Default + ']')
    if ([string]::IsNullOrWhiteSpace($ans)) { return $Default }
    return $ans.Trim()
}
$Q  = [char]34
$SQ = [char]39
$Commands = @('copy', 'write', 'delete', 'list', 'help')

function Show-Help {
    Write-Host ''
    Write-Host '启动项小工具  (HKCU\Software\Microsoft\Windows\CurrentVersion\Run)' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  用法: set_run.ps1 <命令> [名字] [-User 用户名] [-Path 目录]' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  命令:' -ForegroundColor Green
    Write-Host '    copy    [名字]   生成单行命令并复制到剪贴板（默认）'
    Write-Host '                     不检查文件是否存在，可凭空生成'
    Write-Host '    write   [名字]   写入 Run 键，实现登录自启（要求文件存在）'
    Write-Host '    delete  [名字]   删除 Run 项（需确认）'
    Write-Host '    list            列出 Cache 里的 exe（含隐藏）与 Run 现有项'
    Write-Host '    help            显示本帮助'
    Write-Host ''
    Write-Host '  [名字] 可以是: 进程名(SvcMonitor)、序号(2)、或完整路径' -ForegroundColor DarkGray
    Write-Host ('  用户名: 只给名字时会询问，回车 = 当前用户 ' + $CurrentUser) -ForegroundColor DarkGray
    Write-Host '         也可 -User 指定，或直接给完整路径跳过询问' -ForegroundColor DarkGray
    Write-Host '   目标机不是本机时（如 WDKRemoteUser），直接输用户名或 -User 即可' -ForegroundColor DarkGray
    Write-Host '   也可 -Path 直接指定目录，例如 -Path C:\Users\WDKRemoteUser\AppData\Local\Cache' -ForegroundColor DarkGray
    Write-Host '  copy 时若名字不存在，会按 %LOCALAPPDATA%\Cache\<名字>.exe 生成' -ForegroundColor DarkGray
    Write-Host '  省略 [名字] 会进入交互选择' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  例子:' -ForegroundColor Green
    Write-Host '    .\set_run.ps1                    (默认 copy，交互选择)'
    Write-Host '    .\set_run.ps1 copy SvcMonitor    (文件不存在也能复制)'
    Write-Host '    .\set_run.ps1 copy LocaleMgr -User WDKRemoteUser'
    Write-Host '    .\set_run.ps1 copy LocaleMgr -Path C:\Users\X\AppData\Local\Cache'
    Write-Host '    .\set_run.ps1 write LocaleMgr'
    Write-Host '    .\set_run.ps1 delete LocaleMgr'
    Write-Host '    .\set_run.ps1 list'
    Write-Host ''
}

function Get-Candidates {
    param([string]$Dir)
    if ([string]::IsNullOrWhiteSpace($Dir)) { $Dir = Get-UserCacheDir }
    if (-not (Test-Path -LiteralPath $Dir)) { return @() }
    return @(Get-ChildItem -LiteralPath $Dir -Filter *.exe -File -Force -ErrorAction SilentlyContinue |
             Sort-Object LastWriteTime -Descending)
}

function Show-Candidates {
    param($Items)
    if ($Items.Count -eq 0) {
        Write-Host '   (Cache 目录里没有 exe)' -ForegroundColor DarkGray
        return
    }
    for ($i = 0; $i -lt $Items.Count; $i++) {
        $f = $Items[$i]
        $tag = ''
        if (($f.Attributes -band [IO.FileAttributes]::Hidden) -ne 0) { $tag = '  [隐藏]' }
        Write-Host ('   [{0}] {1,-20} {2,10:N0}  {3:yyyy-MM-dd HH:mm}{4}' -f ($i + 1), $f.Name, $f.Length, $f.LastWriteTime, $tag)
    }
}

function Get-RunItems {
    $prop = Get-ItemProperty -Path $RunKey -ErrorAction SilentlyContinue
    if ($null -eq $prop) { return @() }
    $list = @()
    foreach ($n in $prop.PSObject.Properties.Name) {
        if ($n -like 'PS*') { continue }
        $v = [string]$prop.$n
        $list += [pscustomobject]@{
            Name   = $n
            Value  = $v
            Target = $v.Trim($Q)
            Exists = (Test-Path -LiteralPath $v.Trim($Q))
        }
    }
    return $list
}

function Show-RunItems {
    param($Items)
    if ($Items.Count -eq 0) {
        Write-Host '   (Run 键里没有项)' -ForegroundColor DarkGray
        return
    }
    for ($i = 0; $i -lt $Items.Count; $i++) {
        $it = $Items[$i]
        $mark = ''
        if (-not $it.Exists) { $mark = '  [路径失效]' }
        Write-Host ('   [{0}] {1,-24} {2}{3}' -f ($i + 1), $it.Name, $it.Value, $mark)
    }
}

# 解析目标路径。AllowMissing=$true 时名字不存在也返回推算路径（用于 copy）
function Resolve-Exe {
    param([string]$InputName, $Items, [string]$Dir, [switch]$AllowMissing)

    if ([string]::IsNullOrWhiteSpace($InputName)) { return $null }

    $raw = $InputName.Trim()

    $n = 0
    if ([int]::TryParse($raw, [ref]$n)) {
        if ($n -ge 1 -and $n -le $Items.Count) { return $Items[$n - 1].FullName }
        throw "序号超出范围：$n"
    }

    if (Test-Path -LiteralPath $raw) {
        return (Get-Item -LiteralPath $raw -Force).FullName
    }

    $leaf = $raw
    if (-not [IO.Path]::HasExtension($leaf)) { $leaf = $leaf + '.exe' }

    $hit = $Items | Where-Object { $_.Name -ieq $leaf } | Select-Object -First 1
    if ($hit) { return $hit.FullName }

    $hit = $Items | Where-Object { $_.Name -like ('*' + $leaf + '*') } | Select-Object -First 1
    if ($hit) { return $hit.FullName }

    if ($AllowMissing) {
        # 名字不存在：按规则推算一个路径，不做存在性校验
        if ([IO.Path]::IsPathRooted($raw)) {
            if (-not [IO.Path]::HasExtension($raw)) { return ($raw + '.exe') }
            return $raw
        }
        return (Join-Path $Dir $leaf)
    }

    throw "找不到目标：$InputName（可用 list 查看候选；只想复制命令请用 copy）"
}

function Resolve-RunItem {
    param([string]$InputName, $Items)

    if ([string]::IsNullOrWhiteSpace($InputName)) { return $null }

    $n = 0
    if ([int]::TryParse($InputName.Trim(), [ref]$n)) {
        if ($n -ge 1 -and $n -le $Items.Count) { return $Items[$n - 1] }
        throw "序号超出范围：$n"
    }

    $hit = $Items | Where-Object { $_.Name -ieq $InputName.Trim() } | Select-Object -First 1
    if ($hit) { return $hit }

    $leaf = $InputName.Trim()
    if (-not [IO.Path]::HasExtension($leaf)) { $leaf = $leaf + '.exe' }
    $hit = $Items | Where-Object { [IO.Path]::GetFileName($_.Target) -ieq $leaf } | Select-Object -First 1
    if ($hit) { return $hit }

    throw "Run 键里没有该项：$InputName"
}

function Set-ClipText {
    param([string]$Text)
    try { Set-Clipboard -Value $Text -ErrorAction Stop; return $true } catch {}
    try { $Text | clip.exe; return $true } catch {}
    return $false
}

function Build-Command {
    param([string]$ValueName, [string]$Target)
    return 'Set-ItemProperty ' + $SQ + $RunKey + $SQ + ' -Name ' + $ValueName +
           ' -Value ' + $SQ + $Q + $Target + $Q + $SQ + ' -Type String'
}

# RequireExists=$false 时，路径不存在只提示，不判失败（copy 模式）
function Test-Value {
    param([string]$ValueName, [string]$Target, [switch]$RequireExists)

    # 只有真正写入过（RequireExists）才回读注册表；copy 模式一律校验“将要写入的内容”
    if ($RequireExists) {
        $readback = [string](Get-ItemProperty -Path $RunKey).$ValueName
        $kind = (Get-Item -Path $RunKey).GetValueKind($ValueName)
    } else {
        $readback = $Q + $Target + $Q
        $kind = 'String (未写入，仅预览)'
    }

    $hasLF = $readback -match "`n"
    $hasCR = $readback -match "`r"
    $exists = Test-Path -LiteralPath $readback.Trim($Q)
    $expected = $Target.Length + 2

    Write-Host '=== 校验 ===' -ForegroundColor Green
    Write-Host ('   回读值   : ' + $readback)
    Write-Host ('   长度     : ' + $readback.Length + '  (期望 ' + $expected + ')')
    Write-Host ('   含 LF    : ' + $hasLF)
    Write-Host ('   含 CR    : ' + $hasCR)
    Write-Host ('   类型     : ' + $kind)
    if ($RequireExists) {
        Write-Host ('   路径存在 : ' + $exists)
    } else {
        if ($exists) {
            Write-Host '   路径存在 : True'
        } else {
            Write-Host '   路径存在 : False（copy 模式忽略，仅提示）' -ForegroundColor Yellow
        }
    }
    Write-Host ''

    $bad = $false
    if ($hasLF) { $bad = $true }
    if ($hasCR) { $bad = $true }
    if ($readback.Length -ne $expected) { $bad = $true }
    if ($RequireExists -and (-not $exists)) { $bad = $true }

    if ($bad) {
        Write-Host '校验未通过（命令内容有误）。' -ForegroundColor Red
        return $false
    }
    return $true
}

# ---------------- 参数解析 ----------------

$action = 'copy'
$target = $null

$arg0 = ''
if (-not [string]::IsNullOrWhiteSpace($Command)) { $arg0 = $Command.Trim().ToLower() }

if ($Commands -contains $arg0) {
    $action = $arg0
    $target = $Name
} elseif (-not [string]::IsNullOrWhiteSpace($arg0)) {
    $action = 'copy'
    $target = $Command
}


# ---------------- help ----------------

if ($action -eq 'help') {
    Show-Help
    return
}

# ---------------- list ----------------

if ($action -eq 'list') {
    $dir = Get-UserCacheDir -UserName $User -Override $Path
    Write-Host ''
    Write-Host ('=== ' + $dir + ' 里的 exe（含隐藏）===') -ForegroundColor Green
    Show-Candidates -Items (Get-Candidates -Dir $dir)
    Write-Host ''
    Write-Host '=== Run 键现有项 ===' -ForegroundColor Green
    Show-RunItems -Items (Get-RunItems)
    Write-Host ''
    return
}

# ---------------- delete ----------------

if ($action -eq 'delete') {
    $runItems = @(Get-RunItems)
    Write-Host ''
    Write-Host '=== 删除 Run 项 ===' -ForegroundColor Green

    if (-not $target) {
        if ($runItems.Count -eq 0) {
            Write-Host '   Run 键里没有项，无需删除。' -ForegroundColor Yellow
            return
        }
        Show-RunItems -Items $runItems
        Write-Host ''
        $target = Read-Host '   输入要删除的序号或值名'
        if ([string]::IsNullOrWhiteSpace($target)) {
            Write-Host '未输入，已取消。' -ForegroundColor Yellow
            return
        }
    }

    $item = Resolve-RunItem -InputName $target -Items $runItems
    Write-Host ''
    Write-Host ('   将删除: ' + $item.Name + ' = ' + $item.Value) -ForegroundColor Yellow
    $confirm = Read-Host '   确认删除? (y/N)'
    if ($confirm -notmatch '^(y|yes)$') {
        Write-Host '已取消。' -ForegroundColor Yellow
        return
    }

    Remove-ItemProperty -Path $RunKey -Name $item.Name -ErrorAction Stop

    $still = Get-ItemProperty -Path $RunKey -Name $item.Name -ErrorAction SilentlyContinue
    if ($null -eq $still) {
        Write-Host ('已删除: ' + $item.Name) -ForegroundColor Green
    } else {
        Write-Host '删除失败，该项仍在。' -ForegroundColor Red
    }
    Write-Host ''
    return
}

# ---------------- copy / write ----------------

Write-Host ''
if ($action -eq 'write') {
    Write-Host '=== 写入启动项 (Run) ===' -ForegroundColor Green
} else {
    Write-Host '=== 复制启动命令到剪贴板 ===' -ForegroundColor Green
}

# 确定用户名：完整路径不需要；否则询问（回车 = 当前用户）
$userName = $null
$isFullPath = ($target -and ($target -match '[\\/]' -or $target -match '^[A-Za-z]:'))

if (-not [string]::IsNullOrWhiteSpace($Path)) {
    # 已用 -Path 指定目录，不再询问
    $userName = $null
} elseif ($isFullPath) {
    $userName = $null
} elseif (-not [string]::IsNullOrWhiteSpace($User)) {
    $userName = $User
} else {
    $userName = Ask-User -Default $CurrentUser
}

$dir = Get-UserCacheDir -UserName $userName -Override $Path
$candidates = @(Get-Candidates -Dir $dir)

if (-not $target) {
    Write-Host ('   目录: ' + $dir) -ForegroundColor DarkGray
    Write-Host '   候选进程（含隐藏）：' -ForegroundColor Cyan
    Show-Candidates -Items $candidates
    Write-Host ''
    if ($action -eq 'write') {
        Write-Host '   输入名字 / 序号 / 完整路径（直接回车 = 第 1 项）' -ForegroundColor Yellow
    } else {
        Write-Host '   输入名字 / 序号 / 完整路径（不存在也能复制；直接回车 = 第 1 项）' -ForegroundColor Yellow
    }
    $target = Read-Host '   名字'
    if ([string]::IsNullOrWhiteSpace($target)) {
        if ($candidates.Count -gt 0) { $target = '1' }
        else { Write-Host '未输入，已退出。' -ForegroundColor Red; return }
    }
}

if ($action -eq 'write') {
    $exe = Resolve-Exe -InputName $target -Items $candidates -Dir $dir
} else {
    $exe = Resolve-Exe -InputName $target -Items $candidates -Dir $dir -AllowMissing
}

$valueName = [IO.Path]::GetFileNameWithoutExtension($exe)
if ([string]::IsNullOrWhiteSpace($valueName)) { $valueName = $target }
$command = Build-Command -ValueName $valueName -Target $exe

Write-Host ''
Write-Host '   目标文件 : ' -NoNewline
Write-Host $exe -ForegroundColor Cyan
Write-Host '   值名称   : ' -NoNewline
Write-Host $valueName -ForegroundColor Cyan
Write-Host ''

if ($command -match "`r" -or $command -match "`n") {
    Write-Host '命令内部含换行，已中止。' -ForegroundColor Red
    return
}

Write-Host '   生成的命令（单行）：' -ForegroundColor Green
Write-Host ('   ' + $command) -ForegroundColor White
Write-Host ''

if ($action -eq 'write') {
    if ($userName -and ($userName -ne $CurrentUser)) {
        Write-Host ('   注意: Run 键属于当前用户 ' + $CurrentUser + '，但指向 ' + $userName + ' 的文件') -ForegroundColor Yellow
        Write-Host '         跨用户自启建议改用计划任务或服务' -ForegroundColor Yellow
        Write-Host ''
    }
    Set-ItemProperty -Path $RunKey -Name $valueName -Value ($Q + $exe + $Q) -Type String
    $ok = Test-Value -ValueName $valueName -Target $exe -RequireExists
} else {
    $ok = Test-Value -ValueName $valueName -Target $exe
}

if (-not $ok) { return }

if ($action -eq 'write') {
    Write-Host ('已写入 Run 键: ' + $valueName + ' = ' + $Q + $exe + $Q) -ForegroundColor Green
} else {
    if (Set-ClipText -Text $command) {
        Write-Host '命令已复制到剪贴板，可直接粘贴。' -ForegroundColor Green
    } else {
        Write-Host '剪贴板写入失败，请手动复制上面的命令。' -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '提示：注销并重新登录后，用 list 查看该项是否仍有效。' -ForegroundColor DarkGray