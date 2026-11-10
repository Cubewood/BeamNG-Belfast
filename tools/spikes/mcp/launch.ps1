# Launch BeamNG.drive with the throwaway QA profile and MCP enabled. Prints PID and foreground-window info.
# Caller must hold H:\BelfastData\gpu.lock and must have checked no BeamNG process is running.
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class FG { [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid); }
"@
function Get-FG { $h=[FG]::GetForegroundWindow(); $sb=New-Object Text.StringBuilder 256; [void][FG]::GetWindowText($h,$sb,256); $p=0; [void][FG]::GetWindowThreadProcessId($h,[ref]$p); "$p '$($sb.ToString())'" }
"foreground before: $(Get-FG)"
$exe = 'H:\SteamLibrary\steamapps\common\BeamNG.drive\Bin64\BeamNG.drive.x64.exe'
$t0 = Get-Date
$p = Start-Process $exe -ArgumentList '-userpath','H:\BeamNG-QA\profile','-enablemcp' -PassThru -WorkingDirectory 'H:\SteamLibrary\steamapps\common\BeamNG.drive'
"PID $($p.Id) started $($t0.ToString('o'))"
$p.Id | Set-Content H:\BelfastData\spikes\mcp\game.pid
1..60 | % { Start-Sleep -Milliseconds 500; if ($_ % 4 -eq 0) { "t+$($_*0.5)s fg: $(Get-FG)" } }

