//! Perfiles de cultivo.
//!
//! En la carpa de pruebas las etapas y sus rangos están escritos en el código
//! (`apps/tent/autocann/control/vpd_math.py`) y son los de un solo cultivo. En
//! la plataforma son datos: cada cultivo trae sus etapas, cuánto dura cada una
//! y qué rango de VPD busca, y el equipo recibe el perfil activo y lo guarda
//! para seguir controlando sin internet.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Range {
    pub min: f64,
    pub max: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Stage {
    pub name: String,
    /// Días esperados. `None` para una etapa abierta, que dura lo que el
    /// cultivador decida (la última, típicamente).
    pub days: Option<u16>,
    pub vpd_kpa: Range,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CropProfile {
    pub id: String,
    pub name: String,
    pub stages: Vec<Stage>,
}

#[derive(Debug, Clone, PartialEq, thiserror::Error)]
pub enum InvalidProfile {
    #[error("el perfil no tiene etapas")]
    NoStages,
    #[error("etapa {stage:?}: rango de VPD {min}–{max} kPa inválido (tiene que estar dentro de 0–{max_vpd} y min < max)", max_vpd = CropProfile::MAX_VPD_KPA)]
    VpdRange { stage: String, min: f64, max: f64 },
    #[error("etapa {0:?}: una etapa abierta sólo puede ser la última")]
    OpenStageNotLast(String),
}

impl CropProfile {
    /// Más que esto no es un cultivo, es un desierto, y probablemente un error
    /// de unidades (hPa por kPa).
    pub const MAX_VPD_KPA: f64 = 4.0;

    /// Un perfil llega al equipo desde la nube, así que el equipo lo valida
    /// antes de usarlo: un perfil roto no puede dejar un humidificador prendido.
    pub fn validate(&self) -> Result<(), InvalidProfile> {
        let Some((_, before_last)) = self.stages.split_last() else {
            return Err(InvalidProfile::NoStages);
        };
        for stage in &self.stages {
            let Range { min, max } = stage.vpd_kpa;
            let ok = min >= 0.0 && max <= Self::MAX_VPD_KPA && min < max;
            if !ok {
                return Err(InvalidProfile::VpdRange {
                    stage: stage.name.clone(),
                    min,
                    max,
                });
            }
        }
        if let Some(open) = before_last.iter().find(|s| s.days.is_none()) {
            return Err(InvalidProfile::OpenStageNotLast(open.name.clone()));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stage(name: &str, days: Option<u16>, min: f64, max: f64) -> Stage {
        Stage {
            name: name.into(),
            days,
            vpd_kpa: Range { min, max },
        }
    }

    fn lettuce() -> CropProfile {
        CropProfile {
            id: "lechuga".into(),
            name: "Lechuga".into(),
            stages: vec![
                stage("plantin", Some(14), 0.4, 0.8),
                stage("crecimiento", None, 0.8, 1.1),
            ],
        }
    }

    #[test]
    fn accepts_a_sane_profile() {
        assert_eq!(lettuce().validate(), Ok(()));
    }

    #[test]
    fn refuses_inverted_or_absurd_ranges() {
        for (min, max) in [(1.0, 0.5), (1.0, 1.0), (-0.1, 1.0), (0.5, 12.0), (f64::NAN, 1.0)] {
            let mut p = lettuce();
            p.stages[0].vpd_kpa = Range { min, max };
            assert!(
                matches!(p.validate(), Err(InvalidProfile::VpdRange { .. })),
                "{min}–{max}"
            );
        }
    }

    #[test]
    fn only_the_last_stage_may_be_open() {
        let mut p = lettuce();
        p.stages[0].days = None;
        assert_eq!(p.validate(), Err(InvalidProfile::OpenStageNotLast("plantin".into())));
        p.stages.clear();
        assert_eq!(p.validate(), Err(InvalidProfile::NoStages));
    }
}
