@echo off
REM ASCII only -- cmd decodes .bat as CP936 and mangles non-ASCII comments.
call D:\Xilinx\2026.1\Vivado\settings64.bat >nul 2>&1
cd /d D:\Desktop\AMD\_cam_build

echo ==== XVLOG ====
call xvlog pl_imx219_probe.v tb_pl_imx219_probe.v
if errorlevel 1 goto :fail

echo ==== XELAB ====
REM Do NOT use -debug typical here: that snapshot makes the xsim kernel fail to
REM launch ("Could not open file mapping object (2)" + EXCEPTION_ACCESS_VIOLATION).
call xelab tb_pl_imx219_probe -s tb_sim
if errorlevel 1 goto :fail

echo ==== XSIM ====
call xsim tb_sim -R
if errorlevel 1 goto :fail

echo ==== SIM DONE ====
goto :eof

:fail
echo ==== SIM FAILED (errorlevel %errorlevel%) ====
