import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

import '../bills/bills_service.dart';
import '../common/pay_source_picker.dart';
import '../common/payment_tracker.dart';
import 'data_service.dart';

const _networks = {'mtn': 'MTN', 'telecel': 'Telecel', 'at': 'AT'};

/// Pick a network, then a bundle.
class DataScreen extends StatefulWidget {
  const DataScreen({super.key});
  @override
  State<DataScreen> createState() => _DataScreenState();
}

class _DataScreenState extends State<DataScreen> {
  late final DataService _service;
  String _telco = 'mtn';
  late Future<List<DataBundle>> _future;

  @override
  void initState() {
    super.initState();
    _service = DataService(context.read<ApiClient>());
    _future = _service.bundles(_telco);
  }

  void _select(String telco) => setState(() {
        _telco = telco;
        _future = _service.bundles(telco);
      });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Buy data')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: SegmentedButton<String>(
              segments: [
                for (final e in _networks.entries)
                  ButtonSegment(value: e.key, label: Text(e.value)),
              ],
              selected: {_telco},
              onSelectionChanged: (s) => _select(s.first),
            ),
          ),
          Expanded(
            child: FutureBuilder<List<DataBundle>>(
              future: _future,
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
                final bundles = snap.data!;
                return ListView(
                  children: [
                    if (bundles.any((b) => b.isSample))
                      Container(
                        margin: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                        padding: const EdgeInsets.all(10),
                        decoration: BoxDecoration(
                          color: const Color(0xFFFFF4E0),
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: const Text(
                          'Sample bundles for testing — live bundles and prices come '
                          'from the network before launch.',
                          style: TextStyle(color: SokoColors.warning, fontSize: 13),
                        ),
                      ),
                    for (final b in bundles)
                      ListTile(
                        title: Text(b.name),
                        subtitle: Text('${b.volume} · ${b.validity}'),
                        trailing: Text('GH₵ ${b.price}',
                            style: const TextStyle(fontWeight: FontWeight.w600)),
                        onTap: () => Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => DataBuyScreen(telco: _telco, bundle: b),
                        )),
                      ),
                  ],
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

/// Confirm the recipient number and how to pay, then buy.
class DataBuyScreen extends StatefulWidget {
  const DataBuyScreen({super.key, required this.telco, required this.bundle});
  final String telco;
  final DataBundle bundle;
  @override
  State<DataBuyScreen> createState() => _DataBuyScreenState();
}

class _DataBuyScreenState extends State<DataBuyScreen> {
  final _phone = TextEditingController();
  String _source = 'mtn';
  bool _busy = false;
  String? _error;
  PaymentResult? _result;

  @override
  void initState() {
    super.initState();
    // Default to buying for yourself; editable to buy for someone else.
    _phone.text = context.read<AuthController>().me?['phone'] as String? ?? '+233';
  }

  Future<void> _buy() async {
    setState(() { _busy = true; _error = null; });
    final api = context.read<ApiClient>();
    final payer = context.read<AuthController>().me?['phone'] as String?;
    final wallet = PaySourcePicker.isWallet(_source);
    try {
      final res = await DataService(api).buy(
        telco: widget.telco,
        phone: _phone.text.trim(),
        bundleCode: widget.bundle.code,
        source: wallet ? 'wallet' : 'momo',
        network: wallet ? null : _source,
        payer: wallet ? null : payer,
        idempotencyKey: 'data-${DateTime.now().microsecondsSinceEpoch}',
      );
      setState(() => _result = res);
    } catch (e) {
      setState(() => _error = apiErrorMessage(e, fallback: 'Could not buy this bundle.'));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final b = widget.bundle;
    return Scaffold(
      appBar: AppBar(title: Text(b.name)),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: _result != null
            ? PaymentTracker(initial: _result!, service: BillsService(context.read<ApiClient>()))
            : ListView(
                children: [
                  Card(
                    child: ListTile(
                      title: Text(b.name),
                      subtitle: Text('${b.volume} · ${b.validity}'),
                      trailing: Text('GH₵ ${b.price}',
                          style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
                    ),
                  ),
                  const SizedBox(height: 16),
                  TextField(
                    controller: _phone,
                    keyboardType: TextInputType.phone,
                    decoration: InputDecoration(
                        labelText: 'Number to receive the data (${_networks[widget.telco]})'),
                  ),
                  const SizedBox(height: 12),
                  PaySourcePicker(value: _source, onChanged: (v) => setState(() => _source = v)),
                  if (_error != null) ...[
                    const SizedBox(height: 12),
                    Text(_error!, style: const TextStyle(color: SokoColors.danger)),
                  ],
                  const SizedBox(height: 24),
                  ElevatedButton(
                    onPressed: _busy ? null : _buy,
                    child: _busy
                        ? const SizedBox(height: 20, width: 20,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : Text('Buy for GH₵ ${b.price}'),
                  ),
                ],
              ),
      ),
    );
  }
}
