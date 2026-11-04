@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Namothaj - Local Forms Website
echo.
echo ============================================
echo   Namothaj - تشغيل موقع النماذج على جهازك
echo ============================================
echo.
set "FORMS_PYTHON="
py -3 -c "import sys;sys.exit(sys.version_info < (3,10))" >nul 2>nul
if not errorlevel 1 set "FORMS_PYTHON=py -3"
if defined FORMS_PYTHON goto python_found
python -c "import sys;sys.exit(sys.version_info < (3,10))" >nul 2>nul
if not errorlevel 1 set "FORMS_PYTHON=python"
if defined FORMS_PYTHON goto python_found
echo يتطلب التشغيل Python 3.10 أو أحدث.
echo ستفتح صفحة التحميل الرسمية. ثبّت Python وفعّل Add Python to PATH.
echo بعدها شغّل هذا الملف مرة أخرى.
start "" "https://www.python.org/downloads/"
pause
exit /b 1
:python_found
if exist ".venv\Scripts\python.exe" goto install_packages
echo جارٍ تجهيز بيئة التشغيل لأول مرة...
%FORMS_PYTHON% -m venv .venv
if errorlevel 1 goto failed
:install_packages
echo التحقق من متطلبات الموقع... قد يستغرق التشغيل الأول عدة دقائق.
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed
echo.
echo سيُفتح الموقع في المتصفح. اترك هذه النافذة مفتوحة أثناء الاستخدام.
echo لإيقاف الموقع، أغلق النافذة أو اضغط Ctrl+C.
echo.
".venv\Scripts\python.exe" run_local.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo تعذر تشغيل الموقع. صوّر رسالة الخطأ الظاهرة وأرسلها لنا.
pause
exit /b 1
