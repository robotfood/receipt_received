# Privacy Policy

**Last Updated: June 22, 2026**

Your privacy is extremely important to us. This Privacy Policy describes how the Local Receipt to QuickBooks Importer ("Software") handles your information.

Because the Software runs entirely locally on your own computer, we do not collect, store, or share your personal data.

---

### 1. Information We Do Not Collect (Local-First Design)
Unlike traditional cloud services, the Software does not send your data to our servers.
* **Receipt Images & Metadata**: All uploaded receipt/invoice images and their extracted text, vendors, dates, and amounts are stored locally on your machine in the `/data/` folder.
* **Database**: Your receipt logs and processed states are kept in a local SQLite database on your computer.
* **Credentials**: QuickBooks API Client IDs, Client Secrets, and access tokens are stored locally on your machine and never transmitted to us.

### 2. QuickBooks Online Integration (Direct Connection)
* **Direct Transmission**: When you sync or export a receipt to QuickBooks Online, the Software connects directly from your local machine to the official QuickBooks Online API endpoints.
* **Third-Party Control**: The transmission of data to QuickBooks is governed by your own QuickBooks Online Privacy Policy and Terms of Service. No intermediary server intercepts or stores your data.

### 3. Local VLM and OCR Processing
* The Software uses local multimodal models (such as Ollama) and local OCR tools to extract information from receipts. No data is sent to external AI service APIs unless you explicitly configure the Software to use external AI endpoints.

### 4. Data Security
Because all data is stored locally on your computer, the security of your data depends on:
* The security of your personal computer and local network.
* Restricting unauthorized access to your computer.
* Ensuring your local files (specifically the `/data/` folder) are properly protected.

### 5. Changes to This Privacy Policy
We may update this Privacy Policy from time to time. We will notify you of any changes by updating the "Last Updated" date at the top of this document.

### 6. Contact Us
If you have any questions about this Privacy Policy or how your data is handled, you can open an issue on our GitHub repository.
