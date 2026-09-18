// CryptoPriceClient.swift
//
// Клиент спот-цены к публичному API Coinbase на async/await.
// Только Foundation: файл читается и проверяется отдельно, без пакетов и UI.
// Запуск проверок:  swift snippets/CryptoPriceClient.swift
// Разбор типов:     swiftc -typecheck snippets/CryptoPriceClient.swift

import Foundation

// MARK: - Model

/// Пара вида BTC-USD. Инициализатор отсекает мусор до сетевого вызова.
public struct CurrencyPair: Hashable, CustomStringConvertible {
    public let base: String
    public let quote: String

    public init?(base: String, quote: String) {
        let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        let b = base.uppercased(), q = quote.uppercased()
        guard (1...10).contains(b.count), (1...10).contains(q.count),
              b.unicodeScalars.allSatisfy(allowed.contains),
              q.unicodeScalars.allSatisfy(allowed.contains) else { return nil }
        self.base = b
        self.quote = q
    }

    public var description: String { "\(base)-\(quote)" }
}

public struct SpotPrice: Equatable {
    public let pair: CurrencyPair
    /// Decimal, а не Double: цены нельзя считать в плавающей точке.
    public let amount: Decimal
    public let fetchedAt: Date
}

public enum PriceClientError: Error, Equatable {
    case transport(String)
    case badStatus(Int)
    case decoding(String)
    case malformedAmount(String)
}

// MARK: - Protocol

/// Точка подмены: экран или тест получает источник, а не URLSession.
public protocol PriceSource: Sendable {
    func spotPrice(for pair: CurrencyPair) async throws -> SpotPrice
}

// MARK: - Cache

/// Кэш с TTL. actor – потому что к нему тянутся параллельные задачи.
public actor PriceCache {
    private var storage: [CurrencyPair: SpotPrice] = [:]
    private let ttl: TimeInterval

    public init(ttl: TimeInterval = 15) { self.ttl = ttl }

    public func value(for pair: CurrencyPair, now: Date) -> SpotPrice? {
        guard let hit = storage[pair], now.timeIntervalSince(hit.fetchedAt) < ttl else { return nil }
        return hit
    }

    public func store(_ price: SpotPrice) { storage[price.pair] = price }
    public func clear() { storage.removeAll() }
}

// MARK: - Client

public final class CoinbasePriceClient: PriceSource {
    public typealias Transport = @Sendable (URLRequest) async throws -> (Data, URLResponse)

    private struct Envelope: Decodable {
        struct Payload: Decodable { let base: String; let currency: String; let amount: String }
        let data: Payload
    }

    private let baseURL: URL
    private let transport: Transport
    private let cache: PriceCache
    private let clock: @Sendable () -> Date

    public init(baseURL: URL = URL(string: "https://api.coinbase.com/v2/prices")!,
                cache: PriceCache = PriceCache(),
                clock: @escaping @Sendable () -> Date = { Date() },
                transport: Transport? = nil) {
        self.baseURL = baseURL
        self.cache = cache
        self.clock = clock
        self.transport = transport ?? { request in try await URLSession.shared.data(for: request) }
    }

    public func spotPrice(for pair: CurrencyPair) async throws -> SpotPrice {
        if let cached = await cache.value(for: pair, now: clock()) { return cached }

        // Отмена проверяется до и после сетевого вызова: экран мог уже уйти.
        try Task.checkCancellation()
        let request = URLRequest(url: baseURL.appendingPathComponent("\(pair)/spot"))

        let data: Data, response: URLResponse
        do {
            (data, response) = try await transport(request)
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch {
            throw PriceClientError.transport(String(describing: error))
        }
        try Task.checkCancellation()

        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw PriceClientError.badStatus(http.statusCode)
        }

        let envelope: Envelope
        do {
            envelope = try JSONDecoder().decode(Envelope.self, from: data)
        } catch {
            throw PriceClientError.decoding(String(describing: error))
        }

        guard let amount = Decimal(string: envelope.data.amount, locale: Locale(identifier: "en_US")),
              amount > 0 else {
            throw PriceClientError.malformedAmount(envelope.data.amount)
        }

        let price = SpotPrice(pair: pair, amount: amount, fetchedAt: clock())
        await cache.store(price)
        return price
    }
}

// MARK: - Checks

private func ok(_ condition: Bool, _ name: String, _ failures: inout [String]) {
    if condition { print("ok   \(name)") } else { print("FAIL \(name)"); failures.append(name) }
}

private func stub(_ json: String, status: Int = 200, hits: (@Sendable () -> Void)? = nil)
    -> CoinbasePriceClient.Transport {
    { request in
        hits?()
        let response = HTTPURLResponse(url: request.url!, statusCode: status,
                                       httpVersion: nil, headerFields: nil)!
        return (Data(json.utf8), response)
    }
}

private func runChecks() async {
    var failures: [String] = []
    let pair = CurrencyPair(base: "btc", quote: "usd")!
    let body = #"{"data":{"base":"BTC","currency":"USD","amount":"64250.71"}}"#

    ok(pair.description == "BTC-USD", "пара нормализуется к BTC-USD", &failures)
    ok(CurrencyPair(base: "BT C", quote: "USD") == nil, "пробел в тикере отклонён", &failures)

    let price = try? await CoinbasePriceClient(transport: stub(body)).spotPrice(for: pair)
    ok(price?.amount == Decimal(string: "64250.71"), "цена разобрана в Decimal", &failures)

    // Второй вызов не должен уходить в сеть: TTL ещё не истёк.
    let counter = Counter()
    let cached = CoinbasePriceClient(cache: PriceCache(ttl: 60),
                                     transport: stub(body, hits: { counter.bump() }))
    _ = try? await cached.spotPrice(for: pair)
    _ = try? await cached.spotPrice(for: pair)
    ok(counter.value == 1, "второй запрос взят из кэша", &failures)

    do {
        _ = try await CoinbasePriceClient(transport: stub("{}", status: 503)).spotPrice(for: pair)
        ok(false, "код 503 поднимает badStatus", &failures)
    } catch {
        ok(error as? PriceClientError == .badStatus(503), "код 503 поднимает badStatus", &failures)
    }

    do {
        _ = try await CoinbasePriceClient(transport: stub(#"{"data":{"base":"BTC","currency":"USD","amount":"n/a"}}"#))
            .spotPrice(for: pair)
        ok(false, "нечисловая сумма поднимает malformedAmount", &failures)
    } catch {
        ok(error as? PriceClientError == .malformedAmount("n/a"), "нечисловая сумма поднимает malformedAmount", &failures)
    }

    let task = Task { try await CoinbasePriceClient(transport: stub(body)).spotPrice(for: pair) }
    task.cancel()
    do {
        _ = try await task.value
        ok(false, "отменённая задача бросает CancellationError", &failures)
    } catch {
        ok(error is CancellationError, "отменённая задача бросает CancellationError", &failures)
    }

    print(failures.isEmpty ? "\nвсе проверки прошли" : "\nупало: \(failures.joined(separator: ", "))")
}

/// Счётчик обращений к транспорту.
private final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    var value: Int { lock.withLock { count } }
    func bump() { lock.withLock { count += 1 } }
}

await runChecks()
