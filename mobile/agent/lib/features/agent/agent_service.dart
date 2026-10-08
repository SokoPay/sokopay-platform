import 'package:sokopay_shared/sokopay_shared.dart';

/// Agent data access, hitting:
///   GET  /agent/me           → profile + float balance
///   POST /agent/cash-in      {customer_phone, amount}
///   POST /agent/cash-out     {customer_phone, amount}
///   GET  /agent/transactions
class AgentService {
  AgentService(this._api);
  final ApiClient _api;

  Future<AgentProfile> me() async {
    final r = await _api.get('/agent/me');
    return AgentProfile.fromJson(r.data);
  }

  /// Who is this? `account` is what the customer gave: phone or SokoPay wallet ID.
  /// Read the name back to the customer before moving any money.
  Future<CustomerMatch> lookupCustomer(String account) async {
    final r = await _api.get('/agent/customer', query: {'account': account});
    return CustomerMatch(name: r.data['name'] ?? '', phone: r.data['phone'] ?? '');
  }

  Future<void> cashIn(String customer, String amount) =>
      _api.post('/agent/cash-in', data: {'customer': customer, 'amount': amount});

  /// Raises a cash-out REQUEST. Nothing moves until the customer approves with their PIN;
  /// they must have pressed "Cash out" in their app first.
  Future<CashOutReq> requestCashOut(String customer, String amount) async {
    final r = await _api.post('/agent/cash-out', data: {'customer': customer, 'amount': amount});
    return CashOutReq.fromJson(Map<String, dynamic>.from(r.data));
  }

  Future<CashOutReq> cashOutStatus(String id) async =>
      CashOutReq.fromJson(Map<String, dynamic>.from((await _api.get('/agent/cash-out/$id')).data));

  Future<List<AgentTxn>> transactions() async {
    final r = await _api.get('/agent/transactions');
    return (r.data as List).map((e) => AgentTxn.fromJson(e)).toList();
  }

  /// One page of the full history, newest first. Pass the previous page's
  /// [HistoryPage.nextCursor] for more. [kind]: cash_in|cash_out|topup;
  /// [period]: today|7d|30d (null = all time).
  Future<HistoryPage> history({String? kind, String? period, String? cursor}) async {
    final r = await _api.get('/agent/history', query: {
      if (kind != null) 'kind': kind,
      if (period != null) 'period': period,
      if (cursor != null) 'cursor': cursor,
    });
    return HistoryPage.fromJson(Map<String, dynamic>.from(r.data));
  }

  Future<HistoryTxn> transaction(String id) async =>
      HistoryTxn.fromJson(Map<String, dynamic>.from((await _api.get('/agent/transactions/$id')).data));
}

class CustomerMatch {
  CustomerMatch({required this.name, required this.phone});
  final String name; // "Ama M."
  final String phone; // masked
}

class CashOutReq {
  CashOutReq({
    required this.id,
    required this.status,
    required this.amountDisplay,
    required this.expiresAt,
    this.reason = '',
  });
  final String id;
  final String status; // pending | approved | declined | expired | failed
  final String amountDisplay;
  final DateTime expiresAt;
  final String reason;

  factory CashOutReq.fromJson(Map<String, dynamic> j) => CashOutReq(
        id: j['id'],
        status: j['status'] ?? 'pending',
        amountDisplay: j['amount_display'] ?? '',
        expiresAt: DateTime.parse(j['expires_at']).toLocal(),
        reason: j['reason'] ?? '',
      );
}

class HistoryTxn {
  HistoryTxn({
    required this.id,
    required this.reference,
    required this.kind,
    required this.kindDisplay,
    required this.amountDisplay,
    required this.floatEffect,
    required this.customer,
    required this.createdAt,
  });
  final String id;
  final String reference; // AG-… — quote this to support
  final String kind; // cash_in | cash_out | topup
  final String kindDisplay;
  final String amountDisplay;
  final String floatEffect; // "− GH₵ 120.00" (cash-in) / "+ GH₵ 20.00"
  final String customer; // masked by the server; empty for top-ups
  final DateTime createdAt;

  factory HistoryTxn.fromJson(Map<String, dynamic> j) => HistoryTxn(
        id: j['id'],
        reference: j['reference'] ?? '',
        kind: j['kind'] ?? '',
        kindDisplay: j['kind_display'] ?? '',
        amountDisplay: j['amount_display'] ?? '',
        floatEffect: j['float_effect_display'] ?? '',
        customer: j['customer'] ?? '',
        createdAt: DateTime.parse(j['created_at']).toLocal(),
      );
}

class KindTotal {
  KindTotal(this.count, this.total);
  final int count;
  final String total;
  factory KindTotal.fromJson(Map<String, dynamic>? j) =>
      KindTotal(j?['count'] ?? 0, j?['total_display'] ?? 'GH₵ 0.00');
}

class HistorySummary {
  HistorySummary({required this.cashIn, required this.cashOut, required this.topup, required this.float});
  final KindTotal cashIn;
  final KindTotal cashOut;
  final KindTotal topup;
  final String float;
  factory HistorySummary.fromJson(Map<String, dynamic> j) => HistorySummary(
        cashIn: KindTotal.fromJson(Map<String, dynamic>.from(j['cash_in'] ?? {})),
        cashOut: KindTotal.fromJson(Map<String, dynamic>.from(j['cash_out'] ?? {})),
        topup: KindTotal.fromJson(Map<String, dynamic>.from(j['topup'] ?? {})),
        float: j['float_display'] ?? '',
      );
}

class HistoryPage {
  HistoryPage({required this.results, this.nextCursor, this.summary});
  final List<HistoryTxn> results;
  final String? nextCursor;
  final HistorySummary? summary; // first page only
  factory HistoryPage.fromJson(Map<String, dynamic> j) => HistoryPage(
        results: ((j['results'] ?? []) as List)
            .map((e) => HistoryTxn.fromJson(Map<String, dynamic>.from(e)))
            .toList(),
        nextCursor: j['next_cursor'],
        summary: j['summary'] == null ? null : HistorySummary.fromJson(Map<String, dynamic>.from(j['summary'])),
      );
}

class AgentProfile {
  AgentProfile({required this.name, required this.floatDisplay, this.commissionMinor = 0, this.commissionDisplay = ''});
  final String name;
  final String floatDisplay;
  final int commissionMinor; // earned, paid into the float monthly
  final String commissionDisplay;
  factory AgentProfile.fromJson(Map<String, dynamic> j) => AgentProfile(
        name: j['display_name'] ?? '',
        floatDisplay: j['float_display'] ?? 'GH₵ 0.00',
        commissionMinor: (j['commission_minor'] as num?)?.toInt() ?? 0,
        commissionDisplay: j['commission_display'] ?? '',
      );
}

class AgentTxn {
  AgentTxn({required this.kind, required this.customerPhone, required this.amountMinor});
  final String kind;
  final String customerPhone;
  final int amountMinor;
  factory AgentTxn.fromJson(Map<String, dynamic> j) => AgentTxn(
        kind: j['kind'],
        customerPhone: j['customer_phone'] ?? '',
        amountMinor: j['amount_minor'] ?? 0,
      );
}
