import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';
import 'bills_service.dart';
import 'pay_bill_screen.dart';

class BillerListScreen extends StatefulWidget {
  const BillerListScreen({super.key, this.category});
  final String? category;
  @override
  State<BillerListScreen> createState() => _BillerListScreenState();
}

class _BillerListScreenState extends State<BillerListScreen> {
  late final BillsService _service;
  late Future<List<Biller>> _future;

  @override
  void initState() {
    super.initState();
    _service = BillsService(context.read<ApiClient>());
    _future = _service.billers(category: widget.category);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
          title: Text(widget.category == 'airtime' ? 'Airtime & data' : 'Pay a bill')),
      body: FutureBuilder<List<Biller>>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) {
            return const Center(child: Text('Could not load billers.'));
          }
          final billers = snap.data!;
          if (billers.isEmpty) {
            return const Center(child: Text('No billers available.'));
          }
          return ListView.separated(
            itemCount: billers.length,
            separatorBuilder: (_, __) => const Divider(height: 1),
            itemBuilder: (context, i) {
              final b = billers[i];
              return ListTile(
                title: Text(b.name),
                subtitle: Text(b.category),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => PayBillScreen(biller: b, service: _service),
                )),
              );
            },
          );
        },
      ),
    );
  }
}
