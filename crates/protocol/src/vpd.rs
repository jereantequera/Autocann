//! VPD con la misma fórmula que `apps/tent/autocann/control/vpd_math.py`, para
//! que la carpa de pruebas y la plataforma digan el mismo número.

/// Presión de vapor de saturación en kPa (ecuación de Tetens).
pub fn saturation_vapor_pressure_kpa(temperature_c: f64) -> f64 {
    0.6108 * ((17.27 * temperature_c) / (temperature_c + 237.3)).exp()
}

/// Déficit de presión de vapor en kPa. Sin redondear: redondear es cosa de
/// quien lo muestra.
pub fn vpd_kpa(temperature_c: f64, humidity_pct: f64) -> f64 {
    let svp = saturation_vapor_pressure_kpa(temperature_c);
    svp - svp * (humidity_pct / 100.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_the_python_implementation() {
        // round(calculate_vpd(t, rh), 2) en apps/tent.
        for (t, rh, expected) in [(24.5, 62.0, 1.17), (20.0, 50.0, 1.17), (28.0, 70.0, 1.13)] {
            let got = (vpd_kpa(t, rh) * 100.0).round() / 100.0;
            assert_eq!(got, expected, "t={t} rh={rh}");
        }
    }
}
