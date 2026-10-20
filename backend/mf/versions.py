"""Target versions a user may choose: Java and the Spring line that goes with it, per migration target.

Only combinations validated with the pilot are selectable. Java 21 and 25 are the versions offered (17 is no longer
chosen in the UI); a combination that exists but was not validated is listed with `validated: false` and the reason, so
the screens can show it disabled instead of hiding it. The Spring line is not an independent choice: it is fixed by the
Camel version of the target (Camel 4.18.4 is built against Spring 6.2.19 / Spring Boot 3.5), which is why each Spring
option says which Java versions it accepts.
"""
from __future__ import annotations

JAVA_CHOICES = ('21', '25')
NOT_VALIDATED = 'Pendiente de validar con el piloto para este destino'


def tomcat_war() -> dict:
    from .tomcat_war import pipeline
    p = pipeline.load_rules()['profile']
    validated = [v for v in JAVA_CHOICES if v in [str(x) for x in p.get('javaReleases', [])]]
    return {
        'target': 'tomcat-war', 'runtime': 'tomcat', 'label': p['name'],
        'java': [{'version': v, 'validated': v in validated, 'reason': None if v in validated else NOT_VALIDATED} for v in JAVA_CHOICES],
        'spring': [{'key': f"spring-{p['springVersion']}", 'label': f"Spring Framework {p['springVersion']} · Camel {p['camelVersion']}",
                    'java': validated}],
    }


def java25_reason(profile) -> str:
    """Why Java 25 is not offered with this profile, naming the piece that lacks official support and where it is supported."""
    piece = (f'Camel Quarkus {profile.runtime_version}' if profile.runtime == 'quarkus' else f'Camel {profile.camel_version}')
    return f'{NOT_VALIDATED}: {piece} no soporta Java 25 oficialmente; use el perfil Spring Boot 4.1 + Camel 4.22 LTS'


def profile_target(profile) -> dict:
    """Spring Boot / Quarkus profile: the Java releases it declares (profiles.java_versions) are the validated ones. Spring
    Boot 3.5 + Camel 4.14 and Quarkus 3.40: 17/21; Spring Boot 4.1 + Camel 4.22 LTS: 21/25 (verified end to end on the JBoss
    example: Java 25 bytecode, 9/9 cases equal to the legacy system)."""
    from .profiles import java_versions
    validated = [v for v in JAVA_CHOICES if v in java_versions(profile)]
    platform = (f'Spring Boot {profile.runtime_version}' if profile.runtime == 'spring' else f'Quarkus {profile.runtime_version}')
    return {
        'target': 'profile', 'profileId': str(profile.id), 'runtime': profile.runtime, 'label': profile.name,
        'java': [{'version': v, 'validated': v in validated,
                  'reason': None if v in validated else java25_reason(profile) if v == '25' else NOT_VALIDATED}
                 for v in JAVA_CHOICES],
        'spring': [{'key': f'{profile.key}@{profile.version}', 'label': f'{platform} · Camel {profile.camel_version}', 'java': validated}],
    }


def catalog(profiles) -> list[dict]:
    return [tomcat_war()] + [profile_target(p) for p in profiles]


def check(entry: dict, java: str, spring: str | None = None):
    """ValueError with the reason when java (and the Spring line, if given) is not a validated combination of entry."""
    opt = next((j for j in entry['java'] if j['version'] == str(java)), None)
    if opt is None:
        raise ValueError(f"Java {java} no se ofrece; opciones: {', '.join(JAVA_CHOICES)}")
    if not opt['validated']:
        raise ValueError(f"Java {java} con {entry['label']}: {opt['reason']}")
    if spring is not None:
        line = next((s for s in entry['spring'] if s['key'] == spring), None)
        if line is None:
            raise ValueError(f"Versión de Spring {spring} no existe para {entry['label']}")
        if str(java) not in line['java']:
            raise ValueError(f"{line['label']} no está validada con Java {java}")
