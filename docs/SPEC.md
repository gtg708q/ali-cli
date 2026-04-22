# Ali CLI — API Specification

## Authentication

### Login Flow (Browser-based OTP)
1. Start Browser Use cloud session with your configured Alibaba profile ID
2. Navigate to `https://login.alibaba.com/newlogin/icbuLogin.htm`
3. Click "Sign in with a code" button
4. Fill configured email in `input[type="text"]` field
5. Click "Send code" button
6. Wait for OTP email in the configured Gmail inbox
7. Enter 6-digit code in 6 separate `input[type="text"]` boxes (one digit each)
8. Submit → redirects to `https://www.alibaba.com/` on success
9. Save browser storage state + cookies to `~/.ali-cli/`

### Direct API Login (Alternative - not fully working)
- Send code: `POST https://login.alibaba.com/codeLogin/codeSend.do`
  - Body: `loginId={email}&type=email&scene=pc&buyerScene=true`
  - Returns: `{"success":true,"code":101}`
- Login: `POST https://login.alibaba.com/codeLogin/login.do`
  - Body: `code={otp}&type=email&scene=pc&buyerScene=true&loginId={email}`
  - Returns: `{"success":true,"code":101}`
  - Note: Does NOT set proper auth cookies via direct HTTP — browser required

### Key Auth Cookies (on `.alibaba.com`)
| Cookie | Purpose |
|--------|---------|
| `_tb_token_` | CSRF token (used as `ctoken` param in API calls) |
| `cookie2` | Session identifier |
| `xman_f` | User auth token |
| `sgcookie` | Session cookie |
| `t` | Auth token |
| `intl_common_forever` | Persistent auth |
| `havana_lgc2_4` | Login state |
| `XSRF-TOKEN` | CSRF per-subdomain |

### Session Notes
- Sessions expire quickly (~10-30 min inactive)
- The `ctoken` parameter is essential for API calls
- `ctoken` may differ from `_tb_token_` — extracted from page JS context
- Browser Use profile preserves cookies between sessions

## Discovered API Endpoints

### Messenger

#### Unread Summary
```
GET https://onetalk.alibaba.com/message/manager/unread.htm?ctoken={ctoken}&dmtrack_pageid={pageid}&params={json}
```

Response:
```json
{
  "code": "200",
  "data": {
    "messagerUser": true,
    "overseaPaidMember": false,
    "hasLogin": true,
    "listUrl": "https://message.alibaba.com/message/messenger.htm",
    "unreadCount": 0,
    "list": [
      {
        "loginId": "<seller_login_id>",
        "companyName": "Example Seller Co., Ltd.",
        "accountIdEncrypt": "MC1IDX18atMY6skIpy-Oj2sz...",
        "unreadCount": 6,
        "mute": false,
        "fullPortrait": "//sc04.alicdn.com/kf/...",
        "accountId": 284889514,
        "stayTop": false,
        "chatType": "..."
      }
    ]
  },
  "message": "request success."
}
```

#### Micro-Frontend Endpoints
- `GET https://onetouch.alibaba.com/faas/micro-front?appGroup=sc-assets&appName=trade-order&name=micro-service/trade-list-op-buyer`
- `GET https://onetouch.alibaba.com/faas/micro-front?appGroup=icbu-im&appName=im-cloud-drive-cdn&name=micro-service/buyer-dialog`

### RFQ Page
- Main page: `https://mysourcing.alibaba.com/rfq/request/rfq_manage_list.htm`
- Redirects to: `https://message.alibaba.com/message/buyingLeads.htm?from=rfqlist&activeTab=rfq`

### Other
- Environment check: `GET https://login.alibaba.com/getEnvironment.do?experimentKey={key}`
- Device registration: `POST https://login.alibaba.com/get_device.do` (scene=pc)

## Architecture

The CLI uses a hybrid approach:
1. **Login**: Browser Use cloud browser with Alibaba profile + Playwright
2. **Data access**: Local headless Playwright with saved session cookies
3. **Session persistence**: Playwright storage state + raw cookies saved to `~/.ali-cli/`

The direct HTTP API approach (requests library) doesn't work reliably because:
- Alibaba's auth system requires browser fingerprinting (`bx-ua` parameter)
- The `ctoken` is often generated dynamically in page JS, not just from cookies
- CORS and anti-bot measures block plain HTTP API calls

Therefore, all data scraping uses Playwright browser automation with saved session state.
