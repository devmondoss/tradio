@echo off
echo Iniciando servidor RBF Review...
start http://localhost:8765/rbf_review.html
python -m http.server 8765
pause
