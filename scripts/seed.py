"""Carrega dados de demonstração.

Uso:
    python scripts/seed.py           # só popula se o banco estiver vazio
    python scripts/seed.py --reset   # apaga tudo e recria
"""

import datetime as dt
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.models import Attendance, ClassGroup, Instructor, Lesson, Student, User, Workshop  # noqa: E402
from app.services.auth import hash_password  # noqa: E402
from app.services.organizations import get_or_create_default_organization  # noqa: E402

START = dt.date(2026, 7, 1)

DRAWING_TOPICS = [
    ("Apresentação da oficina e sondagem: desenho livre com lápis grafite.", None),
    ("Linhas, formas geométricas e exercícios de controle do traço.", "Exercícios curtos e repetidos funcionaram bem com o grupo."),
    ("Luz e sombra: escala de valores tonais com lápis 2B e 6B.", None),
    ("Introdução à perspectiva: linha do horizonte e ponto de fuga.", None),
    ("Perspectiva com um ponto de fuga aplicada a objetos da sala.", "Demonstração no quadro antes da prática individual ajudou na compreensão."),
    ("Desenho de observação: natureza-morta com garrafas e frutas.", None),
    ("Proporção da figura humana com manequim articulado.", None),
    ("Perspectiva com dois pontos de fuga: fachadas e ruas do bairro.", "Parte da turma teve dificuldade com dois pontos de fuga; retomar na próxima aula."),
    ("Textura e hachura: exercícios com caneta nanquim.", None),
    ("Retrato: estrutura do rosto e posição dos olhos, nariz e boca.", None),
    ("Desenho de observação ao ar livre no pátio do centro comunitário.", None),
    ("Revisão e início dos trabalhos para a mostra de fim de semestre.", None),
]
GUITAR_TOPICS = [
    ("Postura, partes do violão e afinação.", None),
    ("Acordes maiores C, G e D; troca lenta entre acordes.", None),
    ("Ritmo básico de batida para baixo e para cima.", "Uso de metrônomo no celular ajudou a manter o tempo."),
    ("Acordes menores Am e Em; primeira música com quatro acordes.", None),
    ("Leitura de cifras e prática de repertório escolhido pela turma.", None),
    ("Dedilhado simples com polegar alternado.", None),
]
OCCURRENCES = {
    ("Desenho — 14h30", 9): "Falta de energia no espaço por 30 minutos; aula continuou com luz natural.",
    ("Desenho — 14h30", 17): "Chegaram 20 blocos de papel canson doados pela associação.",
    ("Desenho — 14h30", 22): "Faltaram lápis 6B para toda a turma; alunos revezaram o material.",
    ("Violão — 10h00", 4): "Duas cordas arrebentaram; violões ficaram sem uso até a troca.",
    ("Desenho — 16h00", 7): "Discussão entre dois alunos resolvida em conversa com o grupo.",
}


def lesson_days(weekdays: set[int], until: dt.date) -> list[dt.date]:
    days, day = [], START
    while day < until:
        if day.weekday() in weekdays:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


def create_students(db, class_group, rows):
    students = []
    for name, enrolled, dropout in rows:
        s = Student(
            organization_id=class_group.organization_id,
            name=name,
            class_group=class_group,
            enrollment_date=enrolled,
            dropout_date=dropout,
            status="desistente" if dropout else "ativo",
        )
        db.add(s)
        students.append(s)
    return students


def create_lessons(db, rng, class_group, students, days, topics, created_by=None):
    presence_chance = {s.name: rng.uniform(0.55, 0.97) for s in students}
    for index, day in enumerate(days):
        summary, methodology = topics[index % len(topics)]
        lesson = Lesson(
            organization_id=class_group.organization_id,
            class_group=class_group,
            date=day,
            summary=summary,
            methodology=methodology,
            occurrences=OCCURRENCES.get((class_group.name, index)),
            created_by=created_by,
            updated_by=created_by,
        )
        for s in students:
            if not s.is_enrolled_on(day):
                continue
            roll = rng.random()
            if roll < presence_chance[s.name]:
                status = "presente"
            elif roll < presence_chance[s.name] + 0.05:
                status = "atestado"
            else:
                status = "ausente"
            lesson.attendances.append(Attendance(student=s, status=status))
        db.add(lesson)


def seed(reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(engine)
    init_db()
    with SessionLocal() as db:
        org = get_or_create_default_organization(db)  # idempotente: reutiliza se já existe
        if db.scalar(select(Workshop.id).limit(1)):
            print("Banco já possui dados. Use --reset para recriar.")
            return

        rng = random.Random(42)
        today = dt.date.today()
        oid = org.id
        joao = Instructor(organization_id=oid, name="João Pereira", email="joao@example.org")
        ana = Instructor(organization_id=oid, name="Ana Costa", email="ana@example.org")
        admin = User(
            organization_id=oid,
            name="Coordenação Demo",
            email="admin@example.com",
            password_hash=hash_password("AdminDemo123!"),
            role="admin",
        )
        professor1 = User(
            organization_id=oid,
            name="João Pereira",
            email="professor1@example.com",
            password_hash=hash_password("Professor1Demo123!"),
            role="professor",
            instructor=joao,
        )
        professor2 = User(
            organization_id=oid,
            name="Ana Costa",
            email="professor2@example.com",
            password_hash=hash_password("Professor2Demo123!"),
            role="professor",
            instructor=ana,
        )

        desenho = Workshop(organization_id=oid, name="Desenho", language="Artes Visuais", location="Centro Comunitário Vila Nova", instructor=joao)
        violao = Workshop(organization_id=oid, name="Violão", language="Música", location="Associação de Moradores Jardim Esperança", instructor=ana)

        d1430 = ClassGroup(organization_id=oid, workshop=desenho, name="Desenho — 14h30", schedule="Terças e quintas, 14h30")
        d1600 = ClassGroup(organization_id=oid, workshop=desenho, name="Desenho — 16h00", schedule="Terças, 16h00")
        v1000 = ClassGroup(organization_id=oid, workshop=violao, name="Violão — 10h00", schedule="Sábados, 10h00")
        db.add_all([admin, professor1, professor2, joao, ana, desenho, violao, d1430, d1600, v1000])

        jul1, D = START, dt.date
        s1430 = create_students(db, d1430, [
            ("Maria Santos", jul1, None),
            ("João Silva", jul1, None),
            ("Pedro Oliveira", jul1, None),
            ("Ana Beatriz Souza", jul1, None),
            ("Lucas Ferreira", jul1, D(2026, 9, 8)),       # desistência em setembro
            ("Júlia Rodrigues", jul1, None),
            ("Gabriel Almeida", jul1, D(2026, 8, 20)),     # desistência em agosto
            ("Helena Lima", jul1, None),
            ("Rafael Carvalho", D(2026, 8, 10), None),     # matrícula em agosto
            ("Beatriz Gomes", D(2026, 9, 2), None),        # matrícula em setembro
        ])
        s1600 = create_students(db, d1600, [
            ("Carlos Eduardo Nunes", jul1, None),
            ("Fernanda Ribeiro", jul1, None),
            ("Mateus Barbosa", jul1, None),
            ("Sofia Martins", D(2026, 8, 4), None),
            ("Thiago Rocha", jul1, D(2026, 8, 28)),
            ("Isabela Castro", D(2026, 9, 1), None),
        ])
        s1000 = create_students(db, v1000, [
            ("Eduardo Pires", jul1, None),
            ("Camila Teixeira", jul1, None),
            ("Vinícius Moreira", jul1, None),
            ("Letícia Araújo", jul1, D(2026, 9, 5)),
            ("Bruno Cardoso", D(2026, 8, 1), None),
            ("Mariana Duarte", jul1, None),
            ("Gustavo Mendes", D(2026, 9, 12), None),
            ("Patrícia Freitas", jul1, None),
        ])
        db.flush()

        # Aulas até ontem: a aula de hoje fica para ser registrada na interface.
        create_lessons(db, rng, d1430, s1430, lesson_days({1, 3}, today), DRAWING_TOPICS, professor1)
        create_lessons(db, rng, d1600, s1600, lesson_days({1}, today), DRAWING_TOPICS, professor1)
        create_lessons(db, rng, v1000, s1000, lesson_days({5}, today), GUITAR_TOPICS, professor2)
        db.commit()

        lessons = db.scalar(select(Lesson.id).order_by(Lesson.id.desc()).limit(1))
        print(f"Dados de demonstração criados (organização demo): 3 usuários, 2 oficinas, 3 turmas, 24 alunos, {lessons} aulas.")


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
