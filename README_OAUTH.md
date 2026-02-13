# LannaVeg Flask (Google OAuth Ready)

## 1) Install
```bash
pip install -r requirements.txt
```

## 2) Set environment variables (Windows PowerShell)
```powershell
$env:GOOGLE_CLIENT_ID="YOUR_CLIENT_ID"
$env:GOOGLE_CLIENT_SECRET="YOUR_CLIENT_SECRET"
# optional (must match Google Console redirect URI)
$env:GOOGLE_REDIRECT_URI="http://localhost:5000/auth/google/callback"
```

## 3) Run
```bash
python app.py
```

Open: http://localhost:5000

## Notes
- Frontend 'Login with Google' now redirects to `/auth/google` (real consent screen).
- After login, backend stores the Google user into `users` table (provider=google).
- Session is exposed via `/api/me` and the UI auto-syncs on page load.


## NGROK (กันเปิดลิงก์ผิดจน NXDOMAIN)
- ให้รัน `start_local.bat` แล้วรัน `start_ngrok.bat` (ไฟล์นี้จะคัดลอก URL เต็ม + เปิดเบราว์เซอร์ให้อัตโนมัติ)
- ดูหน้า ngrok ได้ที่ `http://127.0.0.1:4040`
