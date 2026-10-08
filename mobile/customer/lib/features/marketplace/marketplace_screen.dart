import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

import 'marketplace_service.dart';

/// Insurance and loans offered by licensed partners. SokoPay forwards the application
/// with the customer's explicit consent; the partner decides.
class MarketplaceScreen extends StatelessWidget {
  const MarketplaceScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 5,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('Financial services'),
          actions: [
            IconButton(
              tooltip: 'My applications',
              icon: const Icon(Icons.assignment),
              onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute(builder: (_) => const ApplicationsScreen())),
            ),
          ],
          bottom: const TabBar(
            isScrollable: true,
            labelColor: Colors.white,
            unselectedLabelColor: Colors.white70,
            indicatorColor: SokoColors.orange,
            tabs: [Tab(text: 'Insurance'), Tab(text: 'Loans'), Tab(text: 'Savings'), Tab(text: 'Invest'),
              Tab(text: 'Pension')],
          ),
        ),
        body: const TabBarView(children: [
          _ProductList(category: 'insurance'),
          _ProductList(category: 'lending'),
          _ProductList(category: 'savings'),
          _ProductList(category: 'investment'),
          _ProductList(category: 'pension'),
        ]),
      ),
    );
  }
}

class _ProductList extends StatelessWidget {
  const _ProductList({required this.category});
  final String category;

  @override
  Widget build(BuildContext context) {
    final service = MarketplaceService(context.read<ApiClient>());
    return FutureBuilder<List<FinancialProduct>>(
      future: service.products(category),
      builder: (context, snap) {
        if (snap.connectionState != ConnectionState.done) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snap.hasError) {
          return Center(child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text(apiErrorMessage(snap.error!), textAlign: TextAlign.center),
          ));
        }
        final products = snap.data!;
        if (products.isEmpty) {
          return const Center(child: Text('No products yet.'));
        }
        return ListView(
          padding: const EdgeInsets.all(12),
          children: [
            for (final p in products)
              Card(
                child: ListTile(
                  title: Text(p.name, style: const TextStyle(fontWeight: FontWeight.w600)),
                  subtitle: Text('${p.provider}\n${p.summary}'),
                  isThreeLine: true,
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => ProductApplyScreen(product: p))),
                ),
              ),
          ],
        );
      },
    );
  }
}

class ProductApplyScreen extends StatefulWidget {
  const ProductApplyScreen({super.key, required this.product});
  final FinancialProduct product;
  @override
  State<ProductApplyScreen> createState() => _ProductApplyScreenState();
}

class _ProductApplyScreenState extends State<ProductApplyScreen> {
  final _amount = TextEditingController();
  bool _consent = false;
  bool _busy = false;
  String? _error;
  Map<String, dynamic>? _result;

  Future<void> _apply() async {
    setState(() { _busy = true; _error = null; });
    try {
      final r = await MarketplaceService(context.read<ApiClient>())
          .apply(widget.product.code, _amount.text.trim(), _consent);
      setState(() => _result = r);
    } catch (e) {
      setState(() => _error = apiErrorMessage(e, fallback: 'Could not send your application.'));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.product;
    final isLoan = p.category == 'lending';
    return Scaffold(
      appBar: AppBar(title: Text(p.name)),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: _result != null
            ? Center(
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    const Icon(Icons.check_circle, color: Colors.green, size: 64),
                    const SizedBox(height: 16),
                    Text('Application sent to ${p.provider}',
                        textAlign: TextAlign.center,
                        style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 8),
                    Text('Ref ${_result!['reference']}',
                        style: const TextStyle(color: Colors.black45)),
                    const SizedBox(height: 8),
                    Text('${p.provider} will review it and contact you.',
                        textAlign: TextAlign.center),
                    const SizedBox(height: 24),
                    ElevatedButton(
                      onPressed: () => Navigator.of(context).pop(),
                      child: const Text('Done'),
                    ),
                  ],
                ),
              )
            : ListView(
                children: [
                  Text(p.summary),
                  const SizedBox(height: 12),
                  Text('Provided by ${p.provider}', style: const TextStyle(fontWeight: FontWeight.w600)),
                  Text('Regulated by ${p.regulator}', style: const TextStyle(color: Colors.black54)),
                  if (p.min != null || p.max != null) ...[
                    const SizedBox(height: 8),
                    Text('From ${p.min ?? '—'} to ${p.max ?? '—'}',
                        style: const TextStyle(color: Colors.black54)),
                  ],
                  const SizedBox(height: 16),
                  TextField(
                    controller: _amount,
                    keyboardType: const TextInputType.numberWithOptions(decimal: true),
                    decoration: InputDecoration(
                      labelText: isLoan ? 'Loan amount (GH₵)' : 'Cover amount (GH₵, optional)',
                      prefixText: 'GH₵ ',
                    ),
                  ),
                  const SizedBox(height: 16),
                  CheckboxListTile(
                    value: _consent,
                    onChanged: (v) => setState(() => _consent = v ?? false),
                    controlAffinity: ListTileControlAffinity.leading,
                    contentPadding: EdgeInsets.zero,
                    title: Text(
                      'I agree that SokoPay may share my name and phone number with '
                      '${p.provider} so they can process this application. '
                      '${p.provider} — not SokoPay — makes the decision.',
                      style: const TextStyle(fontSize: 14),
                    ),
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 8),
                    Text(_error!, style: const TextStyle(color: SokoColors.danger)),
                  ],
                  const SizedBox(height: 16),
                  ElevatedButton(
                    onPressed: (_busy || !_consent) ? null : _apply,
                    child: _busy
                        ? const SizedBox(height: 20, width: 20,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : const Text('Apply'),
                  ),
                ],
              ),
      ),
    );
  }
}

class ApplicationsScreen extends StatefulWidget {
  const ApplicationsScreen({super.key});
  @override
  State<ApplicationsScreen> createState() => _ApplicationsScreenState();
}

class _ApplicationsScreenState extends State<ApplicationsScreen> {
  late final MarketplaceService _service;
  late Future<List<Application>> _future;

  @override
  void initState() {
    super.initState();
    _service = MarketplaceService(context.read<ApiClient>());
    _future = _service.applications();
  }

  Future<void> _payPremium(Application a) async {
    final controller = TextEditingController();
    final amount = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Pay premium from wallet'),
        content: TextField(
          controller: controller,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: const InputDecoration(labelText: 'Amount (GH₵)', prefixText: 'GH₵ '),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(ctx, controller.text.trim()),
              child: const Text('Pay')),
        ],
      ),
    );
    if (amount == null || amount.isEmpty || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await _service.payPremium(a.reference, amount);
      messenger.showSnackBar(const SnackBar(content: Text('Premium paid from your wallet.')));
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
    }
  }

  Future<void> _move(Application a, {required bool payIn}) async {
    final controller = TextEditingController();
    final amount = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(payIn ? 'Pay in from wallet' : 'Withdraw to wallet'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(
            controller: controller,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(labelText: 'Amount (GH₵)', prefixText: 'GH₵ '),
          ),
          if (!payIn)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Text('The provider pays it into your wallet once they process it.',
                  style: TextStyle(fontSize: 12, color: SokoColors.inkMuted)),
            ),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(ctx, controller.text.trim()), child: const Text('Continue')),
        ],
      ),
    );
    controller.dispose();
    if (amount == null || amount.isEmpty || !mounted) return;
    final pin = await askPinFor(context, action: '${payIn ? 'pay in' : 'withdraw'} GH₵ $amount');
    if (pin == null || !mounted) return;
    final messenger = ScaffoldMessenger.of(context);
    try {
      if (payIn) {
        await _service.contribute(a.reference, amount, pin);
      } else {
        await _service.withdraw(a.reference, amount, pin);
      }
      messenger.showSnackBar(SnackBar(content: Text(payIn ? 'Paid in from your wallet.' : 'Withdrawal requested.')));
      setState(() => _future = _service.applications());
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('My applications')),
      body: FutureBuilder<List<Application>>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) return Center(child: Text(apiErrorMessage(snap.error!)));
          final rows = snap.data!;
          if (rows.isEmpty) return const Center(child: Text('No applications yet.'));
          return ListView.separated(
            itemCount: rows.length,
            separatorBuilder: (_, __) => const Divider(height: 1),
            itemBuilder: (_, i) {
              final a = rows[i];
              final canPayPremium = a.category == 'insurance' && a.status == 'approved';
              if (a.isSavingsLike && a.status == 'approved') {
                final pos = a.position ?? const {};
                return ListTile(
                  title: Text(a.product),
                  subtitle: Text('${a.provider} · paid in ${pos['paid_in_display'] ?? '—'}'
                      '${pos['pending_withdrawal_display'] != null ? ' · withdrawal of ${pos['pending_withdrawal_display']} pending' : ''}'),
                  trailing: Wrap(spacing: 4, children: [
                    TextButton(onPressed: () => _move(a, payIn: true), child: const Text('Pay in')),
                    if (a.category != 'pension')
                      TextButton(onPressed: () => _move(a, payIn: false), child: const Text('Withdraw')),
                  ]),
                );
              }
              return ListTile(
                title: Text(a.product),
                subtitle: Text('${a.provider} · ${a.reference}'),
                trailing: canPayPremium
                    ? TextButton(onPressed: () => _payPremium(a), child: const Text('Pay premium'))
                    : Text(a.status),
              );
            },
          );
        },
      ),
    );
  }
}
