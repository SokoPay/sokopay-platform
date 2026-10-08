import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:qr_flutter/qr_flutter.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../business/merchant_service.dart';

/// The counter QR: customers scan it in the SokoPay app and type the amount.
/// Shown full-screen and bright so it scans easily off the phone; the short code
/// underneath is for customers who can't scan.
class StaticQrScreen extends StatefulWidget {
  const StaticQrScreen({super.key});
  @override
  State<StaticQrScreen> createState() => _StaticQrScreenState();
}

class _StaticQrScreenState extends State<StaticQrScreen> {
  late Future<StaticQr> _qr;

  @override
  void initState() {
    super.initState();
    _qr = MerchantService(context.read<ApiClient>()).staticQr();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(title: const Text('Counter QR')),
      body: FutureBuilder<StaticQr>(
        future: _qr,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) {
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(apiErrorMessage(snap.error!), textAlign: TextAlign.center),
              ),
            );
          }
          final qr = snap.data!;
          return ListView(
            padding: const EdgeInsets.all(24),
            children: [
              Text(qr.merchantName,
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
              const SizedBox(height: 4),
              const Text('Scan with the SokoPay app to pay',
                  textAlign: TextAlign.center, style: TextStyle(color: SokoColors.inkMuted)),
              const SizedBox(height: 20),
              Center(
                child: QrImageView(
                  data: qr.payload,
                  version: QrVersions.auto,
                  size: 280,
                  errorCorrectionLevel: QrErrorCorrectLevel.M,
                  semanticsLabel: 'Payment QR code for ${qr.merchantName}',
                ),
              ),
              const SizedBox(height: 20),
              const Text("Can't scan? Enter this code",
                  textAlign: TextAlign.center, style: TextStyle(color: SokoColors.inkMuted)),
              const SizedBox(height: 6),
              GestureDetector(
                onTap: () {
                  Clipboard.setData(ClipboardData(text: qr.shortCode));
                  ScaffoldMessenger.of(context)
                      .showSnackBar(const SnackBar(content: Text('Code copied')));
                },
                child: Text(
                  qr.shortCode.replaceAllMapped(RegExp(r'.{4}'), (m) => '${m[0]} ').trim(),
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                      fontSize: 30, letterSpacing: 4, fontWeight: FontWeight.w700, fontFamily: 'monospace'),
                ),
              ),
              const SizedBox(height: 24),
              const Text(
                'For a printed sticker, download the QR from the web portal (QR → Download for printing).',
                textAlign: TextAlign.center,
                style: TextStyle(color: SokoColors.inkMuted, fontSize: 12),
              ),
            ],
          );
        },
      ),
    );
  }
}
