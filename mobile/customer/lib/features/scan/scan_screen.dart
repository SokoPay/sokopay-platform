import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:provider/provider.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

import 'pay_merchant_screen.dart';

/// Scan a merchant's SokoPay QR (or type their 8-character code) to pay from the wallet.
class ScanScreen extends StatefulWidget {
  const ScanScreen({super.key});
  @override
  State<ScanScreen> createState() => _ScanScreenState();
}

class _ScanScreenState extends State<ScanScreen> {
  final _controller = MobileScannerController(formats: const [BarcodeFormat.qrCode]);
  final _code = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _resolve(String code) async {
    if (_busy) return;
    final api = context.read<ApiClient>();
    final nav = Navigator.of(context);
    setState(() { _busy = true; _error = null; });
    try {
      final r = await api.get('/pay/resolve', query: {'code': code});
      if (!mounted) return;
      await _controller.stop();
      if (!mounted) return;
      final paid = await nav.push<bool>(MaterialPageRoute(
        builder: (_) => PayMerchantScreen(target: Map<String, dynamic>.from(r.data), code: code),
      ));
      if (!mounted) return;
      if (paid == true) {
        nav.pop();
      } else {
        await _controller.start();
      }
    } catch (e) {
      setState(() => _error = apiErrorMessage(e, fallback: "That isn't a SokoPay payment code."));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Scan to pay')),
      body: Column(
        children: [
          Expanded(
            child: Stack(
              fit: StackFit.expand,
              children: [
                MobileScanner(
                  controller: _controller,
                  onDetect: (capture) {
                    final value = capture.barcodes.firstOrNull?.rawValue;
                    if (value != null && value.isNotEmpty) _resolve(value);
                  },
                ),
                // Viewfinder frame
                Center(
                  child: Container(
                    width: 240, height: 240,
                    decoration: BoxDecoration(
                      border: Border.all(color: SokoColors.orange, width: 3),
                      borderRadius: BorderRadius.circular(16),
                    ),
                  ),
                ),
                if (_busy) const Center(child: CircularProgressIndicator()),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text("Can't scan? Enter the shop's SokoPay code",
                    style: TextStyle(color: Colors.black54, fontSize: 13)),
                const SizedBox(height: 6),
                Row(children: [
                  Expanded(
                    child: TextField(
                      controller: _code,
                      textCapitalization: TextCapitalization.characters,
                      maxLength: 8,
                      decoration: const InputDecoration(hintText: 'e.g. 7KQ2M9XW', counterText: ''),
                    ),
                  ),
                  const SizedBox(width: 8),
                  ElevatedButton(
                    onPressed: _busy ? null : () => _resolve(_code.text.trim()),
                    style: ElevatedButton.styleFrom(minimumSize: const Size(80, 52)),
                    child: const Text('Go'),
                  ),
                ]),
                if (_error != null)
                  Text(_error!, style: const TextStyle(color: SokoColors.danger)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
