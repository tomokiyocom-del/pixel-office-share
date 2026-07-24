# ピクセルオフィス（ぴくおふ）— リアルタイム版

MetaLife風の自作バーチャルオフィス。同じURLを開いた人全員が同じオフィスに入り、
アバター移動・チャット・ステータス・会議室施錠・メガホンがリアルタイムに同期されます。
最大50人。**Python標準ライブラリのみで動作**（追加インストール不要）。

## ファイル構成

```
realtime/
├── server.py        ← サーバー本体（これ1つ）
├── public/
│   └── index.html   ← クライアント（画面）
└── README.md        ← このファイル
```

## ローカルで試す（自分のMacだけ）

```bash
cd ~/Desktop/virtual-office/realtime
python3 server.py
```

→ ブラウザで http://localhost:8933 を開く。
同じWi-Fi内の同僚なら `http://（あなたのMacのIPアドレス）:8933` でも入室可能。

※ Claude Code経由でプレビューする場合はデスクトップ直接配信がmacOSに
ブロックされるため、`/tmp/virtual-office-rt/` にコピーしてから起動しています。

## インターネットに公開する（チーム全員が使えるようにする）

一番かんたんなのは **Render**（レンダー）という無料ホスティングです。
アカウント作成が2つ必要ですが、どちらも無料でクレジットカード不要です。

### STEP 1: GitHubにファイルを置く ✅ 済み

プライベートリポジトリにアップロード済み:
**https://github.com/ruike-rgb/pixel-office**

今後このフォルダのファイルを修正したら、ターミナルで:

```bash
cd ~/Desktop/virtual-office/realtime
git add -A && git commit -m "更新内容" && git push
```

（Claude Codeに「pixel-officeを更新して」と頼めば代行します）

### STEP 2: Renderでサーバーを動かす

1. https://render.com で「Sign in with GitHub」でアカウント作成
2. ダッシュボードで「New +」→「Web Service」
3. STEP 1 で作ったリポジトリを選択
4. 設定はほぼそのまま:
   - **Language**: Python 3
   - **Build Command**: 空欄でOK（`echo ok` などでも可）
   - **Start Command**: `python3 server.py`
   - **Instance Type**: Free
5. 「Create Web Service」→ 数分待つと
   `https://pixel-office-xxxx.onrender.com` のようなURLが発行される
6. そのURLをチームのSlackに貼れば全員入室できます 🎉

### 無料プランの注意点

- **15分間誰もいないとサーバーが眠る** → 朝いちの人は表示に1分ほどかかる
  （2人目からは即表示）。気になるなら月$7の Starter プランで常時起動に
- チャット履歴は既定ではサーバー再起動で消えます（メモリ保存のため）。
  → **ずっと残したい場合は下の「チャット履歴を永続化する」を設定**
- URLを知っていれば誰でも入れます。URLは社外に共有しないこと

## チャット履歴を永続化する（Upstash・無料）

チャットを **Upstash Redis**（無料枠）に保存すると、サーバーが再起動・スリープしても
履歴が残ります。設定しなければ従来どおりメモリのみ（履歴は再起動で消える）で動きます。

### STEP 1: Upstashでデータベースを作る

1. https://upstash.com にアクセス →「Sign Up」（GitHubアカウントでログイン可・無料）
2. 「Create Database」→ **Redis** を選択
   - Name: `pixel-office` など
   - Region: 日本に近い場所（例: Singapore / AP-Northeast があればそれ）
   - Type: **Free**
3. 作成したデータベースのページを下にスクロールし、**「REST API」** の欄にある
   次の2つの値をコピー（`.env` タブの表示が分かりやすい）:
   - `UPSTASH_REDIS_REST_URL`
   - `UPSTASH_REDIS_REST_TOKEN`

### STEP 2: Renderに環境変数として登録

1. Render → `pixel-office` サービス →左メニュー **「Environment」**
2. 「Add Environment Variable」で2つ追加:
   | Key | Value |
   |-----|-------|
   | `UPSTASH_REDIS_REST_URL`   | STEP 1でコピーしたURL |
   | `UPSTASH_REDIS_REST_TOKEN` | STEP 1でコピーしたトークン |
3. 「Save Changes」→ 自動で再デプロイ → 以降チャットが永続化されます 🎉

> トークンは秘密情報です。GitHubには絶対に置かず、必ずRenderの環境変数に入れてください
> （コードはトークンを環境変数からのみ読みます）。
> 起動ログに `Upstash chat persistence: ON` と出れば有効化成功です。

## 案内役（受付ロボット）として入室する

URLのうしろに `?role=guide` を付けて開くと、**水色の浮遊ロボット**（ヘッドセット付き）
として入室します。一般メンバー（オレンジの脚付きキャラ）とはっきり区別され、
メンバー一覧にも「🧭 …（案内役）」と表示されます。案内役は自動離席しません。

```
https://pixel-office-jqsm.onrender.com/?role=guide
```

## 今後の拡張アイデア

- 音声通話（近くの人の声が聞こえる）: LiveKit / Agora などのWebRTCサービスを組み込む
- 入室パスワード
- Slackステータス連携

---
※このリポジトリは会社用(ruike-rgb)と個人用(tomokiyocom-del)の2か所にミラーされています。
`git push` で両方に同時反映されます。
