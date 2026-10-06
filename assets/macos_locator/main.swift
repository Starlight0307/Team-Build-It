// LUMI 위치 도우미 (macOS)
//
// 파이썬 실행 파일에는 위치 권한을 요청할 앱 정보(Info.plist)가 없어서 macOS 위치
// 서비스를 쓸 수 없다. 그래서 위치 권한 문구가 든 작은 앱으로 따로 만들어, Chrome처럼
// 처음 한 번 "위치 허용"을 받고 와이파이 기반 정확한 좌표를 얻는다 (core/weather.py
// _macos_location이 `open`으로 실행한다).
//
// 출력(stdout, 한 줄 JSON): {"lat", "lon", "accuracy", "locality"} 또는 {"error": "denied"|"failed"|"timeout"}
// 빌드: ./build.sh
import CoreLocation
import Foundation

final class Locator: NSObject, CLLocationManagerDelegate {
    let manager = CLLocationManager()
    var done = false

    func start() {
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        switch manager.authorizationStatus {
        case .notDetermined: manager.requestWhenInUseAuthorization()
        case .denied, .restricted: fail("denied")
        default: manager.startUpdatingLocation()
        }
    }

    func locationManagerDidChangeAuthorization(_ m: CLLocationManager) {
        switch m.authorizationStatus {
        case .authorizedAlways: m.startUpdatingLocation()
        case .denied, .restricted: fail("denied")
        default: break
        }
    }

    func locationManager(_ m: CLLocationManager, didUpdateLocations locs: [CLLocation]) {
        guard !done, let loc = locs.last else { return }
        done = true
        m.stopUpdatingLocation()
        // 좌표 → 지명("화성시"). 실패해도 좌표는 돌려준다
        CLGeocoder().reverseGeocodeLocation(loc, preferredLocale: Locale(identifier: "ko_KR")) { marks, _ in
            let p = marks?.first
            self.emit([
                "lat": loc.coordinate.latitude, "lon": loc.coordinate.longitude,
                "accuracy": loc.horizontalAccuracy,
                "locality": p?.locality ?? p?.administrativeArea ?? "",
            ], code: 0)
        }
    }

    func locationManager(_ m: CLLocationManager, didFailWithError e: Error) {
        let code = (e as? CLError)?.code
        if code == .locationUnknown { return }   // 아직 못 잡았을 뿐 — 계속 기다린다
        fail(code == .denied ? "denied" : "failed")
    }

    func fail(_ reason: String) { emit(["error": reason], code: 2) }

    func emit(_ obj: [String: Any], code: Int32) {
        let data = (try? JSONSerialization.data(withJSONObject: obj)) ?? Data("{}".utf8)
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
        exit(code)
    }
}

let locator = Locator()
locator.start()
// 허용 창에 답하는 시간까지 포함해 60초
DispatchQueue.main.asyncAfter(deadline: .now() + 60) { locator.fail("timeout") }
RunLoop.main.run()
