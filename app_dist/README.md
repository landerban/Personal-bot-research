# app_dist — the Desktop App folder, versioned

These are the files installed to `C:\Users\ASUS TUF\Desktop\App`. They live
here too so `git pull` can restore or update them, and so changes to the
launcher are reviewable like any other code.

To refresh the installed copy after a pull:

    copy /Y app_dist\*.bat  "%USERPROFILE%\Desktop\App\"
    copy /Y app_dist\RUNBOOK.md "%USERPROFILE%\Desktop\App\"

The App folder additionally contains `.venv\` and `logs\`, which are machine
state and are deliberately NOT versioned.

Nothing here contains credentials; keys live in
`%USERPROFILE%\.binance_testnet.env`, outside the repo.
