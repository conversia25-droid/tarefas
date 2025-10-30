
# Client PowerShell example - consulta tarefas e executa
$server = "http://192.168.0.10:5000"  # ajuste para o IP do servidor
$host = $env:COMPUTERNAME
$secret = "troque_me_ja"  # deve bater com a SECRET_KEY do servidor
$token = ([System.BitConverter]::ToString((New-Object -TypeName System.Security.Cryptography.SHA256Managed).ComputeHash([System.Text.Encoding]::UTF8.GetBytes($host + $secret)))).Replace("-","").ToLower()

try {
    $url = "$server/api/tasks?host=$host&token=$token"
    $resp = Invoke-RestMethod -Uri $url -Method Get -ErrorAction Stop
    foreach ($t in $resp) {
        Write-Host "Tarefa recebida: $($t.title) - tipo: $($t.type)"
        if ($t.type -eq 'message') {
            # mostra notificação simples
            [reflection.assembly]::LoadWithPartialName("System.Windows.Forms") | Out-Null
            [reflection.assembly]::LoadWithPartialName("System.Drawing") | Out-Null
            $notify = New-Object System.Windows.Forms.NotifyIcon
            $notify.Icon = [System.Drawing.SystemIcons]::Information
            $notify.BalloonTipTitle = $t.title
            $notify.BalloonTipText = $t.payload
            $notify.Visible = $true
            $notify.ShowBalloonTip(8000)
            Start-Sleep -Seconds 10
            $notify.Dispose()
            # confirmar
            $body = @{ task_id = $t.id; host = $host; status = 'success'; message = 'message shown' } | ConvertTo-Json
            Invoke-RestMethod -Uri "$server/api/confirm" -Method Post -ContentType 'application/json' -Body $body -ErrorAction SilentlyContinue
        }
        elseif ($t.type -eq 'command') {
            # AVISO: cuidado ao executar comandos vindos do servidor. Limite o que é permitido no painel.
            try {
                Invoke-Expression $t.payload
                $body = @{ task_id = $t.id; host = $host; status = 'success'; message = 'command executed' } | ConvertTo-Json
                Invoke-RestMethod -Uri "$server/api/confirm" -Method Post -ContentType 'application/json' -Body $body -ErrorAction SilentlyContinue
            } catch {
                $body = @{ task_id = $t.id; host = $host; status = 'error'; message = $_.Exception.Message } | ConvertTo-Json
                Invoke-RestMethod -Uri "$server/api/confirm" -Method Post -ContentType 'application/json' -Body $body -ErrorAction SilentlyContinue
            }
        }
    }
}
catch {
    Write-Host "Erro ao consultar servidor: $_"
}
