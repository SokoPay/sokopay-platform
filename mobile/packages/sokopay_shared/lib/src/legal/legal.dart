import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../core/config.dart';
import '../core/theme.dart';

/// The legal documents (originals in docs/legal; served by the backend at /legal/<slug>).
class LegalDoc {
  const LegalDoc(this.slug, this.title, this.icon);
  final String slug;
  final String title;
  final IconData icon;

  static const privacy = LegalDoc('privacy-policy', 'Privacy Policy', Icons.privacy_tip_outlined);
  static const terms = LegalDoc('terms-of-service', 'Terms of Service', Icons.description_outlined);
  static const merchantTerms = LegalDoc('merchant-terms', 'Merchant Terms', Icons.storefront_outlined);
  static const agentTerms = LegalDoc('agent-terms', 'Agent Terms', Icons.handshake_outlined);
  static const complaints = LegalDoc('complaints-and-disputes', 'Complaints and Disputes', Icons.support_agent);
  static const acceptableUse = LegalDoc('acceptable-use', 'Acceptable Use Policy', Icons.gavel_outlined);

  Uri get url => Uri.parse('${AppConfig.webBaseUrl}/legal/$slug');
}

Future<void> openLegal(BuildContext context, LegalDoc doc) async {
  final messenger = ScaffoldMessenger.of(context);
  final ok = await launchUrl(doc.url, mode: LaunchMode.inAppBrowserView);
  if (!ok) messenger.showSnackBar(SnackBar(content: Text("Couldn't open ${doc.title}.")));
}

/// "Help & legal": how to get help, and the documents that apply to this app.
/// [mainTerms] is the agreement for this app (customer / merchant / agent terms).
class HelpLegalScreen extends StatelessWidget {
  const HelpLegalScreen({super.key, this.mainTerms = LegalDoc.terms});
  final LegalDoc mainTerms;

  @override
  Widget build(BuildContext context) {
    final docs = [mainTerms, LegalDoc.privacy, LegalDoc.complaints, LegalDoc.acceptableUse];
    return Scaffold(
      appBar: AppBar(title: const Text('Help & legal')),
      body: ListView(children: [
        const Padding(
          padding: EdgeInsets.fromLTRB(16, 16, 16, 4),
          child: Text('Need help?', style: TextStyle(fontWeight: FontWeight.w700)),
        ),
        const ListTile(
          leading: Icon(Icons.receipt_long_outlined, color: SokoColors.ink),
          title: Text('Problem with a payment?'),
          subtitle: Text('Open the payment from your activity and tap "Report a problem".'),
        ),
        const ListTile(
          leading: Icon(Icons.mail_outline, color: SokoColors.ink),
          title: Text('Contact SokoPay support'),
          subtitle: Text(AppConfig.supportContact),
        ),
        const ListTile(
          leading: Icon(Icons.shield_outlined, color: SokoColors.danger),
          title: Text('SokoPay will never ask for your PIN'),
          subtitle: Text('Not by phone, SMS or WhatsApp. Never share it, not even with an agent.'),
        ),
        const Divider(),
        const Padding(
          padding: EdgeInsets.fromLTRB(16, 8, 16, 4),
          child: Text('Legal', style: TextStyle(fontWeight: FontWeight.w700)),
        ),
        for (final d in docs)
          ListTile(
            leading: Icon(d.icon, color: SokoColors.ink),
            title: Text(d.title),
            trailing: const Icon(Icons.open_in_new, size: 18),
            onTap: () => openLegal(context, d),
          ),
      ]),
    );
  }
}

/// "By continuing you agree to the Terms and the Privacy Policy" with tappable links.
class LegalConsentText extends StatefulWidget {
  const LegalConsentText({super.key, this.terms = LegalDoc.terms});
  final LegalDoc terms;
  @override
  State<LegalConsentText> createState() => _LegalConsentTextState();
}

class _LegalConsentTextState extends State<LegalConsentText> {
  late final TapGestureRecognizer _terms;
  late final TapGestureRecognizer _privacy;

  @override
  void initState() {
    super.initState();
    _terms = TapGestureRecognizer()..onTap = () => openLegal(context, widget.terms);
    _privacy = TapGestureRecognizer()..onTap = () => openLegal(context, LegalDoc.privacy);
  }

  @override
  void dispose() {
    _terms.dispose();
    _privacy.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    const link = TextStyle(color: SokoColors.orangeDark, decoration: TextDecoration.underline);
    return Text.rich(
      TextSpan(style: const TextStyle(fontSize: 12, color: SokoColors.inkMuted), children: [
        const TextSpan(text: 'By continuing you agree to the '),
        TextSpan(text: widget.terms.title, style: link, recognizer: _terms),
        const TextSpan(text: ' and the '),
        TextSpan(text: 'Privacy Policy', style: link, recognizer: _privacy),
        const TextSpan(text: '.'),
      ]),
      textAlign: TextAlign.center,
    );
  }
}
