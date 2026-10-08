import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';

import '../auth/auth_controller.dart';
import 'app_lock.dart';

/// Security settings shared by the apps: biometric unlock and signing out everywhere.
class SecuritySettingsScreen extends StatelessWidget {
  const SecuritySettingsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Security')),
      body: ListView(children: [
        const BiometricUnlockTile(),
        const Divider(height: 1),
        ListTile(
          leading: const Icon(Icons.pin_outlined),
          title: const Text('Change PIN'),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => context.push('/change-pin'),
        ),
        ListTile(
          leading: const Icon(Icons.person_outline),
          title: const Text('Profile'),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => context.push('/profile'),
        ),
        const Divider(height: 1),
        ListTile(
          leading: const Icon(Icons.devices_other),
          title: const Text('Sign out of all devices'),
          subtitle: const Text('Use this if you lose a phone. You will need to sign in again.'),
          onTap: () async {
            final ok = await showDialog<bool>(
              context: context,
              builder: (c) => AlertDialog(
                title: const Text('Sign out everywhere?'),
                content: const Text('Every phone signed in to your account will be signed out, including this one.'),
                actions: [
                  TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
                  TextButton(onPressed: () => Navigator.pop(c, true), child: const Text('Sign out everywhere')),
                ],
              ),
            );
            if (ok == true && context.mounted) await context.read<AuthController>().signOutEverywhere();
          },
        ),
        ListTile(
          leading: const Icon(Icons.no_accounts_outlined, color: SokoColors.danger),
          title: const Text('Close account', style: TextStyle(color: SokoColors.danger)),
          onTap: () => context.push('/close-account'),
        ),
        const Divider(),
        ListTile(
          leading: const Icon(Icons.help_outline, color: SokoColors.ink),
          title: const Text('Help & legal'),
          subtitle: const Text('Support, terms, privacy, complaints'),
          onTap: () => context.push('/help'),
        ),
      ]),
    );
  }
}
