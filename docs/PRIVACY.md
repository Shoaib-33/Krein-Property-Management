# Privacy (AVG/GDPR) for operators of Haven

This page is for the company that runs Haven. It lists what Haven does for privacy, and what you still need to arrange yourself. It is not legal advice; have it checked by your privacy officer or lawyer.

## What Haven stores

Tenants: name, email, phone, language, tenancies, rent, invoices, payments and receipts, deposit, messages and requests, repairs, documents (contracts, receipts, inspection reports), contract form answers and signatures, portal login. Owners and contractors: name, contact details and their records. Staff: name, email and the audit log of their actions.

## What Haven does

- **Access:** every person only sees their own records (tenant portal, owner portal, contractor portal, landlord view limited to his own units, staff by role).
- **Right of access / portability (art. 15 and 20):** Tenants & contracts > Tenant directory > *Export data* gives a ZIP with all data about one tenant.
- **Erasure after the retention period (art. 17, 5(1)(e)):** `python manage.py privacy-erase --apply` anonymises former tenants whose last tenancy ended more than `HAVEN_RETENTION_YEARS` (default 7, the Dutch fiscal retention period) years ago and who owe nothing. Amounts, dates and invoice numbers stay so the accounts remain correct. Run it regularly (for example monthly, from a scheduled job).
- **Audit log:** kept as the tamper-evident legal record of who did what. It contains names in some entries and is not anonymised.
- **Help chat:** what people type is sent to Groq (USA) or Google (Gemini). Contact details and account numbers are masked before sending and the chat warns people not to type personal details. Turn the AI off for a group with `HAVEN_ASSISTANT_AI_FOR`, or completely by leaving the API keys empty.

## What you need to arrange

1. **Processing agreements (verwerkersovereenkomsten)** with: your hosting provider (Render or Google Cloud), your email provider (SMTP), and, if the help chat AI is on, Groq and/or Google. For Google Gemini use a paid (not free-tier) key: on the free tier Google may use prompts to improve its products.
2. **Transfers outside the EU:** Groq and Google process in the USA. Rely on the EU-US Data Privacy Framework or standard contractual clauses, and mention it in your privacy statement. Choose an EU region for hosting.
3. **Privacy statement** for tenants and owners. A starting text:

   > *[Company] processes your personal data to manage your tenancy: your contact details, the tenancy agreement, rent invoices and payments, messages and repair requests. We use Haven (property management software), hosted by [hosting provider] in [region]. Emails are sent through [email provider]. The help chat in the portal uses an AI service from Groq or Google; do not type personal details in it. We keep financial records for 7 years after your tenancy ends (Dutch fiscal law) and then anonymise your data. You can ask for a copy of your data, or for corrections, at [email address].*

4. **Backups** contain personal data: keep them encrypted and in the EU, and delete old backups on a schedule.
5. **Data breaches:** have a procedure to report to the Autoriteit Persoonsgegevens within 72 hours.
