# panel/routers/admin_seed.py (optionnel temporaire)
@router.post("/admin/seed-features")
def seed(db: Session = Depends(get_db), me=Depends(require_site_admin)):
    data = [
        {"key":"twitch","name":"Twitch","description":"Intégrations Twitch"},
        {"key":"youtube","name":"YouTube","description":"Intégrations YouTube"},
        {"key":"ai","name":"AI","description":"Fonctionnalités IA"},
    ]
    for f in data:
        if not db.query(Feature).filter_by(key=f["key"]).first():
            db.add(Feature(**f))
    db.commit()
    return {"ok": True}
