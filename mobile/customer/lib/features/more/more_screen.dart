import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../wallet/wallet_service.dart';

/// More tab: profile (name, phone, wallet ID), account & security settings, sign out.
class MoreScreen extends StatefulWidget {
  const MoreScreen({super.key});
  @override
  State<MoreScreen> createState() => _MoreScreenState();
}

class _MoreScreenState extends State<MoreScreen> {
  String? _walletNumber;

  @override
  void initState() {
    super.initState();
    WalletService(context.read<ApiClient>()).load().then((w) {
      if (mounted) setState(() => _walletNumber = w.walletNumber);
    }).catchError((_) {});
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    final me = auth.me ?? const {};
    final name = (me['full_name'] as String?)?.trim();
    Widget item(IconData icon, String title, String route) => ListTile(
          leading: Icon(icon, color: SokoColors.ink),
          title: Text(title),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => context.push(route),
        );
    return Scaffold(
      appBar: AppBar(title: const Text('More')),
      body: ListView(children: [
        Container(
          margin: const EdgeInsets.all(16),
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(color: SokoColors.ink, borderRadius: BorderRadius.circular(14)),
          child: Row(children: [
            const CircleAvatar(radius: 26, backgroundColor: SokoColors.orange,
                child: Icon(Icons.person, color: Colors.white)),
            const SizedBox(width: 14),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(name == null || name.isEmpty ? 'SokoPay customer' : name,
                    style: const TextStyle(color: Colors.white, fontSize: 17, fontWeight: FontWeight.w700)),
                Text(me['phone'] ?? '', style: const TextStyle(color: Colors.white70)),
                if (_walletNumber != null && _walletNumber!.isNotEmpty)
                  GestureDetector(
                    onTap: () {
                      Clipboard.setData(ClipboardData(text: _walletNumber!.replaceAll(' ', '')));
                      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Wallet ID copied')));
                    },
                    child: Text('Wallet ID $_walletNumber  ⧉', style: const TextStyle(color: SokoColors.orange)),
                  ),
              ]),
            ),
          ]),
        ),
        item(Icons.person_outline, 'Profile', '/profile'),
        item(Icons.verified_user_outlined, 'Limits & verification', '/kyc'),
        item(Icons.receipt_long_outlined, 'Statements', '/statement'),
        item(Icons.notifications_none, 'Notifications', '/inbox'),
        item(Icons.health_and_safety_outlined, 'Insurance & loans', '/marketplace'),
        const Divider(),
        const BiometricUnlockTile(),
        item(Icons.lock_outline, 'Security', '/security'),
        item(Icons.help_outline, 'Help & legal', '/help'),
        const Divider(),
        ListTile(
          leading: const Icon(Icons.logout, color: SokoColors.danger),
          title: const Text('Sign out', style: TextStyle(color: SokoColors.danger)),
          onTap: () => context.read<AuthController>().signOut(),
        ),
        const SizedBox(height: 100),
      ]),
    );
  }
}
