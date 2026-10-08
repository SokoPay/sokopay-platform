import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../core/brand.dart';

import 'auth_controller.dart';
import '../legal/legal.dart';

/// Step 1 of sign-in: enter the phone number to receive a one-time code.
///
/// [tagline] lets each app show its own subtitle under the SOKOPAY wordmark.
class PhoneScreen extends StatefulWidget {
  const PhoneScreen({super.key, this.tagline = 'Secure payments', this.terms = LegalDoc.terms});
  final String tagline;
  /// The agreement for this app (customer, merchant or agent terms), linked under the button.
  final LegalDoc terms;
  @override
  State<PhoneScreen> createState() => _PhoneScreenState();
}

class _PhoneScreenState extends State<PhoneScreen> {
  final _controller = TextEditingController(text: '+233');

  String get _phone => _controller.text.trim();
  bool get _valid => RegExp(r'^\+233\d{9}$').hasMatch(_phone);

  Future<void> _submit(AuthController auth) async {
    if (await auth.requestOtp(_phone) && mounted) {
      context.push('/otp', extra: _phone);
    }
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SizedBox(height: 48),
              const Center(child: SokoLogo(height: 44)),
              const SizedBox(height: 8),
              Text(widget.tagline,
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.black54)),
              const SizedBox(height: 48),
              const Text("What's your phone number?",
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
              const SizedBox(height: 8),
              TextField(
                controller: _controller,
                keyboardType: TextInputType.phone,
                decoration: const InputDecoration(hintText: '+233244058519'),
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: 8),
              const Text("We'll text you a 6-digit code.",
                  style: TextStyle(color: Colors.black45, fontSize: 13)),
              if (auth.error != null) ...[
                const SizedBox(height: 12),
                Text(auth.error!, style: const TextStyle(color: Colors.red)),
              ],
              const Spacer(),
              LegalConsentText(terms: widget.terms),
              const SizedBox(height: 12),
              ElevatedButton(
                onPressed: (!_valid || auth.loading) ? null : () => _submit(auth),
                child: auth.loading
                    ? const SizedBox(
                        height: 20, width: 20,
                        child: CircularProgressIndicator(
                            strokeWidth: 2, color: Colors.white))
                    : const Text('Send code'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
