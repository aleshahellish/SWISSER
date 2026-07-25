@echo off
chcp 65001 >nul
title TAO MEXC Test v2
cd /d "%~dp0"
python tao_mexc_test.py
echo.
echo Нажмите любую клавишу, чтобы закрыть окно...
pause >nul
