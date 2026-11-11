# =============================================================================
# init_db.py — INICIALIZADOR DO BANCO DE DADOS DA CORRETORA
# -----------------------------------------------------------------------------
# Execute no terminal:
#   python init_db.py           # Cria tabelas e estrutura se não existirem
#   python init_db.py --reset   # Limpa e recria todas as tabelas em estado limpo
# =============================================================================

import sys
import database

def main():
    forcar = "--reset" in sys.argv
    print("=" * 60)
    print("  Corretora de Seguros — Inicializador de Banco de Dados")
    print("=" * 60)
    if forcar:
        print("[AVISO] Modo --reset ativado: recriando banco limpo para produção...")
    else:
        print("[INFO] Verificando e criando estrutura de tabelas...")

    database.init_db(forcar_recriar=forcar)

    conn = database.get_db()
    cursor = conn.cursor()

    tabelas = [
        "usuarios", "cadastros", "quadros", "seguros",
        "sinistros", "cards", "tarefas", "documentos_negociacao",
        "historico_eventos", "logs_auditoria", "configuracoes"
    ]

    print("\nEstatísticas do Banco de Dados ('corretora.db'):")
    print("-" * 60)
    for t in tabelas:
        cursor.execute(f"SELECT COUNT(*) FROM {t};")
        qtd = cursor.fetchone()[0]
        print(f"  • {t.ljust(25)}: {qtd} registro(s)")
    print("-" * 60)
    print("Banco de dados pronto para uso diário da corretora com sucesso!\n")
    conn.close()

if __name__ == "__main__":
    main()
