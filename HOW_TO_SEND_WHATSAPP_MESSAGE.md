# How to Send a WhatsApp Message

This guide explains the common ways to send a WhatsApp message: from the WhatsApp app, from WhatsApp Web, through a click-to-chat link, and programmatically using the WhatsApp Business Cloud API.

## 1. Send a Message from the WhatsApp Mobile App

Use this method when you want to send a normal personal or business message manually.

1. Open WhatsApp on your phone.
2. Tap the chat icon.
3. Select an existing contact or search for a contact.
4. Type your message.
5. Tap Send.

To send a message to a new number:

1. Save the number in your phone contacts, including the country code.
2. Open WhatsApp.
3. Start a new chat.
4. Search for the saved contact.
5. Type and send your message.

Example phone number format:

```text
+91 98765 43210
```

## 2. Send a Message from WhatsApp Web

Use this method when you want to send messages from a computer.

1. Open https://web.whatsapp.com.
2. Open WhatsApp on your phone.
3. Go to Linked devices.
4. Scan the QR code shown in the browser.
5. Open a chat.
6. Type your message and press Enter.

## 3. Send a Message Without Saving the Number

WhatsApp supports click-to-chat links. This lets you open a chat with a phone number without saving it first.

Use this format:

```text
https://wa.me/<country_code_and_phone_number>
```

Example:

```text
https://wa.me/919876543210
```

To include a pre-filled message:

```text
https://wa.me/919876543210?text=Hello%20there
```

Notes:

- Do not include `+`, spaces, brackets, or dashes in the link number.
- Include the country code.
- The user still needs to press Send inside WhatsApp.

## 4. Send a Message Programmatically

Use this method when you want an application, website, CRM, or backend service to send WhatsApp messages automatically.

For automation, use the WhatsApp Business Platform Cloud API from Meta.

Official docs:

- Meta WhatsApp Cloud API getting started: https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started
- Send messages API: https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages
- Message templates: https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/overview

### Requirements

You need:

- A Meta developer account.
- A Meta Business account.
- A WhatsApp Business Account.
- A registered WhatsApp business phone number.
- A phone number ID.
- A WhatsApp access token.
- An approved message template for business-initiated messages.

### Important Rule

If a customer messages your business first, you can usually reply within the customer service window using normal text messages.

If your business starts the conversation, or messages outside the service window, you usually need to send an approved template message.

## 5. Example API Request: Send a Text Message

Use this when the user has already messaged your business and you are inside the allowed customer service window.

```bash
curl -X POST "https://graph.facebook.com/v23.0/<PHONE_NUMBER_ID>/messages" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "messaging_product": "whatsapp",
    "to": "919876543210",
    "type": "text",
    "text": {
      "body": "Hello, this is a test message."
    }
  }'
```

Replace:

- `<PHONE_NUMBER_ID>` with your WhatsApp phone number ID.
- `<ACCESS_TOKEN>` with your Meta access token.
- `919876543210` with the recipient phone number in international format, digits only.

## 6. Example API Request: Send a Template Message

Use this when your business starts the conversation or needs to send a notification.

```bash
curl -X POST "https://graph.facebook.com/v23.0/<PHONE_NUMBER_ID>/messages" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "messaging_product": "whatsapp",
    "to": "919876543210",
    "type": "template",
    "template": {
      "name": "hello_world",
      "language": {
        "code": "en_US"
      }
    }
  }'
```

The template name and language code must exactly match an approved template in your WhatsApp Business Account.

## 7. Phone Number Format

For API messages, use international format with digits only.

Correct:

```text
919876543210
```

Incorrect:

```text
+91 98765 43210
91-98765-43210
(91) 98765 43210
```

## 8. Common Problems

- Message not sent: check the access token, phone number ID, and recipient number.
- Template rejected: check Meta's template rules and avoid spammy or unclear wording.
- Template not found: confirm the template name and language code.
- Recipient number invalid: use country code and digits only.
- Text message blocked: the customer may not be inside the allowed service window.
- API returns an authorization error: generate a valid token with the required WhatsApp permissions.

## 9. Best Practices

- Only message people who have opted in.
- Keep messages short and useful.
- Use templates for alerts, reminders, confirmations, and updates.
- Do not send spam or unsolicited promotional messages.
- Store access tokens securely. Do not commit them to source control.
- Log API errors so delivery issues are easier to debug.
