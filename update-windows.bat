@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Namothaj - Update
echo أغلق نافذة تشغيل الموقع قبل التحديث.
echo سيُحدّث الكود فقط. بيانات الأوامر والنماذج الأصلية ستبقى على جهازك.
echo.
if not exist ".venv\Scripts\python.exe" (
  echo شغّل run-windows.bat مرة واحدة أولًا، ثم أعد تشغيل التحديث.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" update_local.py
echo.
pause
