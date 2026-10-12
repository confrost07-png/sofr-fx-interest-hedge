@echo off
chcp 65001 > nul
set APP=app\app.py
cd /d "%~dp0"
if not exist "%APP%" (
  echo [ERROR] %APP% not found. Put this file in the project root folder next to app, data, requirements.txt
  pause
  exit /b
)
set PY=py
where py > nul 2> nul || set PY=python
%PY% --version > nul 2> nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install from https://www.python.org/downloads/ and check "Add python.exe to PATH"
  pause
  exit /b
)
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  mkdir "%USERPROFILE%\.streamlit" 2> nul
  echo [general]> "%USERPROFILE%\.streamlit\credentials.toml"
  echo email = "">> "%USERPROFILE%\.streamlit\credentials.toml"
)
echo Installing packages... (first time 1-3 min)
%PY% -m pip install -q -r requirements.txt
echo Starting app... keep this window open. Browser: http://localhost:8501
%PY% -m streamlit run %APP%
pause
