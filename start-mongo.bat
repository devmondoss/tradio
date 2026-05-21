@echo off
:: Arranca un mongod dedicado a flowsurface en el puerto 27018, aislado del
:: mongod por defecto (:27017) que comparte espacio con otras herramientas.
:: Datapath persistente: %LOCALAPPDATA%\flowsurface\mongo-data
::
:: Uso: doble click o `start-mongo.bat` desde PowerShell. Cierra la ventana
:: para detener el mongod. La data sobrevive entre ejecuciones.

setlocal

set "MONGOD=C:\Program Files\MongoDB\Server\8.2\bin\mongod.exe"
set "DBPATH=%LOCALAPPDATA%\flowsurface\mongo-data"
set "LOGPATH=%LOCALAPPDATA%\flowsurface\mongo-data\mongod.log"
set "PORT=27018"

if not exist "%DBPATH%" mkdir "%DBPATH%"

echo Arrancando mongod dedicado de flowsurface...
echo   bin      : %MONGOD%
echo   dbpath   : %DBPATH%
echo   logpath  : %LOGPATH%
echo   port     : %PORT%
echo.
echo Cierra esta ventana para detener mongod.
echo La UI debe usar MONGODB_URI=mongodb://localhost:%PORT% (ya es el default).
echo.

"%MONGOD%" --port %PORT% --dbpath "%DBPATH%" --logpath "%LOGPATH%" --logappend

endlocal
