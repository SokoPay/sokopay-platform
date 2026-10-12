import 'package:dio/dio.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

/// Agent data access, hitting:
///   GET  /agent/me           → profile + float balance
///   POST /agent/cash-in      {customer_phone, amount}
///   POST /agent/cash-out     {customer_phone, amount}
///   GET  /agent/transactions
///   GET  /agent/earnings, POST /agent/earnings/redeem {pin}
///   POST /agent/sell/airtime|data|bill {…, pin, receipt_phone, receipt_email} (Idempotency-Key)
///   GET  /agent/deposit-accounts, GET/POST /agent/deposits (multipart, optional slip photo)
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
  /// [HistoryPage.nextCursor] for more. [kind]: cash_in|cash_out|topup|airtime|data|bill;
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

  // --- earnings ---
  Future<Earnings> earnings({String? cursor}) async => Earnings.fromJson(Map<String, dynamic>.from(
      (await _api.get('/agent/earnings', query: {if (cursor != null) 'cursor': cursor})).data));

  /// Moves every redeemable earning into the float. Returns e.g. "GH₵ 2.00".
  Future<String> redeem(String pin) async =>
      ((await _api.post('/agent/earnings/redeem', data: {'pin': pin})).data as Map)['redeemed'] ?? '';

  // --- selling to customers (paid from the float) ---
  Future<List<SaleBiller>> billers({String? category}) async {
    final r = await _api.get('/billers', query: category != null ? {'category': category} : null);
    return (r.data as List).map((e) => SaleBiller.fromJson(Map<String, dynamic>.from(e))).toList();
  }

  Future<List<SaleBundle>> bundles(String telco) async {
    final r = await _api.get('/telcos/$telco/bundles');
    return (r.data as List).map((e) => SaleBundle.fromJson(Map<String, dynamic>.from(e))).toList();
  }

  /// [kind]: airtime | data | bill. Throws on failure (the float is refunded server-side).
  Future<SaleResult> sell(String kind, Map<String, dynamic> body,
      {required String pin, required String idempotencyKey}) async {
    final r = await _api.post('/agent/sell/$kind',
        data: {...body, 'pin': pin}, headers: {'Idempotency-Key': idempotencyKey});
    return SaleResult.fromJson(Map<String, dynamic>.from(r.data));
  }

  // --- buying float by bank deposit ---
  Future<List<DepositAccount>> depositAccounts() async {
    final r = await _api.get('/agent/deposit-accounts');
    return (r.data as List).map((e) => DepositAccount.fromJson(Map<String, dynamic>.from(e))).toList();
  }

  Future<List<FloatDeposit>> deposits() async {
    final r = await _api.get('/agent/deposits');
    return (r.data as List).map((e) => FloatDeposit.fromJson(Map<String, dynamic>.from(e))).toList();
  }

  Future<FloatDeposit> submitDeposit({
    required String accountId,
    required String amount,
    required String reference,
    required String depositDate, // yyyy-MM-dd
    List<int>? slipJpeg,
  }) async {
    final form = FormData.fromMap({
      'deposit_account': accountId,
      'amount': amount,
      'reference': reference,
      'deposit_date': depositDate,
      if (slipJpeg != null)
        'slip': MultipartFile.fromBytes(slipJpeg, filename: 'slip.jpg', contentType: DioMediaType('image', 'jpeg')),
    });
    final r = await _api.post('/agent/deposits', data: form);
    return FloatDeposit.fromJson(Map<String, dynamic>.from(r.data));
  }
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
    this.commission = '',
    this.paymentReference = '',
    this.token = '',
  });
  final String id;
  final String reference; // AG-… — quote this to support
  final String kind; // cash_in | cash_out | topup | airtime | data | bill
  final String kindDisplay;
  final String amountDisplay;
  final String floatEffect; // "− GH₵ 120.00" (cash-in, sales) / "+ GH₵ 20.00"
  final String customer; // masked by the server; empty for top-ups
  final DateTime createdAt;
  final String commission; // what you earned on it ("" if none)
  final String paymentReference; // SP-… for airtime / data / bill sales
  final String token; // e.g. an ECG prepaid token

  factory HistoryTxn.fromJson(Map<String, dynamic> j) => HistoryTxn(
        id: j['id'],
        reference: j['reference'] ?? '',
        kind: j['kind'] ?? '',
        kindDisplay: j['kind_display'] ?? '',
        amountDisplay: j['amount_display'] ?? '',
        floatEffect: j['float_effect_display'] ?? '',
        customer: j['customer'] ?? '',
        createdAt: DateTime.parse(j['created_at']).toLocal(),
        commission: j['commission_display'] ?? '',
        paymentReference: j['payment_reference'] ?? '',
        token: j['token'] ?? '',
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
  HistorySummary(
      {required this.cashIn, required this.cashOut, required this.topup, required this.salesCount, required this.float});
  final KindTotal cashIn;
  final KindTotal cashOut;
  final KindTotal topup;
  final int salesCount; // airtime + data + bills sold
  final String float;
  factory HistorySummary.fromJson(Map<String, dynamic> j) {
    KindTotal k(String key) => KindTotal.fromJson(Map<String, dynamic>.from(j[key] ?? {}));
    return HistorySummary(
      cashIn: k('cash_in'),
      cashOut: k('cash_out'),
      topup: k('topup'),
      salesCount: k('airtime').count + k('data').count + k('bill').count,
      float: j['float_display'] ?? '',
    );
  }
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
  AgentProfile(
      {required this.name,
      required this.floatDisplay,
      this.commissionMinor = 0,
      this.commissionDisplay = '',
      this.redeemableDisplay = ''});
  final String name;
  final String floatDisplay;
  final int commissionMinor; // earned and not yet in the float
  final String commissionDisplay;
  final String redeemableDisplay; // the part you can redeem now
  factory AgentProfile.fromJson(Map<String, dynamic> j) => AgentProfile(
        name: j['display_name'] ?? '',
        floatDisplay: j['float_display'] ?? 'GH₵ 0.00',
        commissionMinor: (j['commission_minor'] as num?)?.toInt() ?? 0,
        commissionDisplay: j['commission_display'] ?? '',
        redeemableDisplay: j['redeemable_display'] ?? '',
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

// --- earnings ---------------------------------------------------------------------------------
class Earnings {
  Earnings(
      {required this.redeemable,
      required this.redeemableMinor,
      required this.awaitingPayout,
      required this.paid,
      required this.nextPayoutDue,
      required this.results,
      this.nextCursor});
  final String redeemable; // cash-in and sales commission: redeem any time
  final int redeemableMinor;
  final String awaitingPayout; // cash-out commission: paid by SokoPay every two weeks
  final String paid;
  final DateTime? nextPayoutDue;
  final List<EarningRow> results;
  final String? nextCursor;
  factory Earnings.fromJson(Map<String, dynamic> j) => Earnings(
        redeemable: j['redeemable'] ?? '',
        redeemableMinor: (j['redeemable_minor'] as num?)?.toInt() ?? 0,
        awaitingPayout: j['awaiting_payout'] ?? '',
        paid: j['paid'] ?? '',
        nextPayoutDue: j['next_payout_due'] == null ? null : DateTime.parse(j['next_payout_due']),
        results: ((j['results'] ?? []) as List)
            .map((e) => EarningRow.fromJson(Map<String, dynamic>.from(e)))
            .toList(),
        nextCursor: j['next_cursor'],
      );
}

class EarningRow {
  EarningRow(
      {required this.productDisplay,
      required this.reference,
      required this.transactionAmount,
      required this.amount,
      required this.status,
      required this.createdAt});
  final String productDisplay;
  final String reference; // AG-…
  final String transactionAmount;
  final String amount;
  final String status; // redeemable | awaiting_payout | paid
  final DateTime createdAt;
  factory EarningRow.fromJson(Map<String, dynamic> j) => EarningRow(
        productDisplay: j['product_display'] ?? '',
        reference: j['transaction_reference'] ?? '',
        transactionAmount: j['transaction_amount'] ?? '',
        amount: j['amount'] ?? '',
        status: j['status'] ?? '',
        createdAt: DateTime.parse(j['created_at']).toLocal(),
      );
}

// --- selling ----------------------------------------------------------------------------------
class SaleBiller {
  SaleBiller({required this.code, required this.name, required this.category});
  final String code;
  final String name;
  final String category;
  factory SaleBiller.fromJson(Map<String, dynamic> j) =>
      SaleBiller(code: j['code'], name: j['name'] ?? '', category: j['category'] ?? '');
}

class SaleBundle {
  SaleBundle({required this.code, required this.name, required this.price, required this.validity});
  final String code;
  final String name;
  final String price;
  final String validity;
  factory SaleBundle.fromJson(Map<String, dynamic> j) =>
      SaleBundle(code: j['code'], name: j['name'] ?? '', price: j['price'] ?? '', validity: j['validity'] ?? '');
}

class SaleResult {
  SaleResult(
      {required this.reference,
      required this.total,
      required this.token,
      required this.floatNow,
      this.commission = '',
      this.txnId});
  final String reference; // SP-…
  final String total; // what was taken from the float (incl. any fee)
  final String token; // e.g. an ECG prepaid token
  final String floatNow;
  final String commission;
  final String? txnId;
  factory SaleResult.fromJson(Map<String, dynamic> j) {
    final t = j['transaction'] as Map?;
    return SaleResult(
      reference: j['reference'] ?? '',
      total: j['total'] ?? '',
      token: j['delivery_token'] ?? '',
      floatNow: j['float'] ?? '',
      commission: t?['commission_display'] ?? '',
      txnId: t?['id'],
    );
  }
}

// --- buying float -------------------------------------------------------------------------------
class DepositAccount {
  DepositAccount(
      {required this.id, required this.bankName, required this.accountName, required this.accountNumber, this.branch = ''});
  final String id;
  final String bankName;
  final String accountName;
  final String accountNumber;
  final String branch;
  factory DepositAccount.fromJson(Map<String, dynamic> j) => DepositAccount(
      id: '${j['id']}',
      bankName: j['bank_name'] ?? '',
      accountName: j['account_name'] ?? '',
      accountNumber: j['account_number'] ?? '',
      branch: j['branch'] ?? '');
}

class FloatDeposit {
  FloatDeposit(
      {required this.bank,
      required this.amount,
      required this.reference,
      required this.status,
      required this.statusDisplay,
      required this.note,
      required this.submittedAt,
      this.depositDate});
  final String bank;
  final String amount;
  final String reference;
  final String status; // pending | approved | rejected
  final String statusDisplay;
  final String note;
  final DateTime submittedAt;
  final DateTime? depositDate;
  factory FloatDeposit.fromJson(Map<String, dynamic> j) => FloatDeposit(
        bank: j['bank'] ?? '',
        amount: j['amount'] ?? '',
        reference: j['reference'] ?? '',
        status: j['status'] ?? '',
        statusDisplay: j['status_display'] ?? '',
        note: j['note'] ?? '',
        submittedAt: DateTime.parse(j['submitted_at']).toLocal(),
        depositDate: j['deposit_date'] == null ? null : DateTime.parse(j['deposit_date']),
      );
}
