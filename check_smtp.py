import getpass
import smtplib

address = input("Gmailアドレス: ").strip()
raw_password = getpass.getpass("アプリパスワード(入力は非表示です): ")
app_password = "".join(raw_password.split())  # 見えないスペース類を含めて全て除去
print(f"(入力された文字数: {len(app_password)}文字。16文字であるはずです)")

try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(address, app_password)
    print("成功: ログインできました。")
except smtplib.SMTPAuthenticationError as e:
    print("失敗:", e)
