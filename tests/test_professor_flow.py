"""v0.2.1 etapa 3: fluxo do professor + mobile. Cobre o que mudou de comportamento
(não estilo/CSS, que é validado manualmente): home acionável, class_id
pré-selecionado, isolamento mantido, nav por perfil, mensagens consistentes."""

from app.services import auth, catalog


def _login_professor(client, db, setup, workshop_ids=None):
    professor = client.post(
        "/api/users",
        json={
            "name": "Professor", "email": "professor@example.test", "password": "senha-professor-123",
            "role": "professor", "workshop_ids": workshop_ids if workshop_ids is not None else [setup.workshop.id],
        },
    )
    assert professor.status_code == 201, professor.text
    client.post("/logout")
    login = client.post(
        "/login", data={"email": "professor@example.test", "password": "senha-professor-123"}, follow_redirects=False
    )
    assert login.status_code == 303
    return professor.json()


# ------------------------------------------------------------------- home
def test_professor_home_lists_each_class_with_a_direct_register_link(client, db, setup):
    other_class = catalog.create_class(db, {"workshop_id": setup.workshop.id, "name": "Turma B"}, setup.org_id)
    _login_professor(client, db, setup)

    page = client.get("/")
    assert page.status_code == 200
    for class_id in (setup.klass.id, other_class["id"]):
        assert f'/aulas/registrar?class_id={class_id}' in page.text
    # sem hero genérico duplicado para o professor (uma única ação por turma)
    assert 'href="/aulas/registrar">＋ Registrar aula</a>' not in page.text


def test_professor_home_does_not_list_classes_from_other_organizations(client, db, setup, org):
    from app.models import Organization

    other_org = Organization(name="Outra", slug="outra-prof-flow")
    db.add(other_org)
    db.commit()
    other_workshop = catalog.create_workshop(db, {"name": "Oficina Alheia"}, other_org.id)
    foreign_class = catalog.create_class(db, {"workshop_id": other_workshop["id"], "name": "Turma Alheia"}, other_org.id)

    _login_professor(client, db, setup)
    page = client.get("/")
    assert f'/aulas/registrar?class_id={foreign_class["id"]}' not in page.text
    assert "Alheia" not in page.text


def test_admin_home_keeps_generic_hero_button(client):
    page = client.get("/")
    assert 'href="/aulas/registrar">＋ Registrar aula</a>' in page.text


def test_professor_without_classes_sees_no_admin_link(client, db, setup):
    _login_professor(client, db, setup, workshop_ids=[])
    page = client.get("/")
    assert "Você ainda não possui turmas atribuídas. Procure a coordenação." in page.text
    assert 'href="/turmas"' not in page.text


# ---------------------------------------------------------- registrar (class_id)
def test_lesson_register_page_renders_for_own_class_id(client, db, setup):
    _login_professor(client, db, setup)
    response = client.get(f"/aulas/registrar?class_id={setup.klass.id}")
    assert response.status_code == 200
    assert setup.klass.id  # sanity: o shell renderiza; o acesso real é validado pela API do roster


def test_roster_api_enforces_access_for_preselected_class_id(client, db, setup):
    """A URL pode trazer qualquer class_id; quem garante o acesso é a API (defesa real)."""
    other_workshop = catalog.create_workshop(db, {"name": "Outra Oficina"}, setup.org_id)
    other_class = catalog.create_class(db, {"workshop_id": other_workshop["id"], "name": "Não atribuída"}, setup.org_id)
    _login_professor(client, db, setup)  # só recebe setup.workshop, não a oficina acima

    assert client.get(f"/aulas/registrar?class_id={other_class['id']}").status_code == 200  # shell sempre renderiza
    assert client.get(f"/api/classes/{other_class['id']}/roster").status_code == 403  # dado real é bloqueado


def test_roster_response_has_workshop_name_for_picker_summary(client, db, setup):
    """lesson_form.html usa workshop_name da resposta para o resumo 'Turma X · Oficina Y'."""
    response = client.get(f"/api/classes/{setup.klass.id}/roster")
    assert response.status_code == 200
    body = response.json()
    assert body["class"]["workshop_name"] == setup.workshop.name


# --------------------------------------------------------------------- nav
def test_professor_nav_prioritizes_register_and_omits_admin_links(client, db, setup):
    _login_professor(client, db, setup)
    page = client.get("/").text
    register_pos = page.index(">Registrar aula<")
    dashboard_pos = page.index(">Dashboard<")
    assert register_pos < dashboard_pos  # registrar aparece antes de dashboard na ordem do professor
    assert "Oficinas e turmas" not in page
    assert ">Usuários<" not in page


def test_admin_nav_unchanged_order(client):
    page = client.get("/").text
    dashboard_pos = page.index(">Dashboard<")
    alunos_pos = page.index(">Alunos<")
    assert dashboard_pos < alunos_pos  # ordem original do admin preservada
    assert "Oficinas e turmas" in page


# ------------------------------------------------------------- mensagens (item 15)
def test_no_oficina_message_is_consistent_between_home_and_lesson_form(client, db, setup):
    _login_professor(client, db, setup, workshop_ids=[])
    home_text = client.get("/").text
    form_text = client.get("/aulas/registrar").text
    assert "Você ainda não possui turmas atribuídas. Procure a coordenação." in home_text
    assert "Você ainda não possui turmas atribuídas. Procure a coordenação." in form_text
    # a decisão de mostrar o link para /turmas é feita em JS por IS_ADMIN; aqui confirmamos
    # que a variável está corretamente "false" para o professor (o link nunca é montado no navegador).
    assert "const IS_ADMIN = false;" in form_text
