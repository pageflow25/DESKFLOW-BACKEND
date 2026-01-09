# Script para corrigir o erro do bcrypt e reiniciar o backend
Write-Host "=== Corrigindo dependências do backend ===" -ForegroundColor Cyan

# Ativar ambiente virtual
Write-Host "`nAtivando ambiente virtual..." -ForegroundColor Yellow
& .\.venv\Scripts\Activate.ps1

# Desinstalar bcrypt atual
Write-Host "`nDesinstalando bcrypt 5.x..." -ForegroundColor Yellow
pip uninstall bcrypt -y

# Reinstalar dependências com versão correta
Write-Host "`nReinstalando dependências..." -ForegroundColor Yellow
pip install -r requirements.txt

Write-Host "`n=== Dependências corrigidas! ===" -ForegroundColor Green
Write-Host "`nAgora você pode iniciar o backend com:" -ForegroundColor Cyan
Write-Host "python main.py" -ForegroundColor White
