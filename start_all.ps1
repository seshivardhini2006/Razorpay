# Launches Reclaim backend (FastAPI) and frontend (Vite) in background windows.
$backend = Start-Process -FilePath "C:\Users\seshi\Downloads\Razorpay\venv\Scripts\python.exe" `
  -ArgumentList "-m","uvicorn","main:app","--port","8000" `
  -WorkingDirectory "C:\Users\seshi\Downloads\Razorpay\backend" -PassThru
$frontend = Start-Process -FilePath "cmd.exe" `
  -ArgumentList "/c","npm run dev" `
  -WorkingDirectory "C:\Users\seshi\Downloads\Razorpay\frontend" -PassThru

Write-Host "Backend  running (PID $($backend.Id))  -> http://localhost:8000"
Write-Host "Frontend running (PID $($frontend.Id)) -> http://localhost:5173"
Write-Host ""
Write-Host "Open http://localhost:5173 and click 'Stream failure events'."