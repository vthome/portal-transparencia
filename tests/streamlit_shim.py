"""Permite rodar o AppTest do Streamlit sem o pyarrow instalado (ambientes de teste mínimos).

Só age quando `import pyarrow` falha: troca a serialização Arrow das tabelas (st.dataframe) por
pickle, o suficiente para o AppTest montar e ler as tabelas. Com pyarrow instalado (o normal, e o
caso do Streamlit Cloud) não faz nada.
"""
import pickle
import sys
import types


class _PyarrowFalso(types.ModuleType):
    __version__ = "0.0.0"

    def __getattr__(self, nome):
        if nome.startswith("__"):
            raise AttributeError(nome)
        cls = type(nome, (), {})          # isinstance(x, pa.Table) etc. -> False
        setattr(self, nome, cls)
        return cls


def instalar() -> bool:
    try:
        import pyarrow  # noqa: F401
        return False
    except ImportError:
        pass
    import pandas  # noqa: F401  (antes do falso: o pandas confere a versão do pyarrow ao ser importado)

    sys.modules["pyarrow"] = _PyarrowFalso("pyarrow")
    from streamlit import dataframe_util as du

    du.convert_pandas_df_to_arrow_bytes = lambda df: pickle.dumps(df)
    du.convert_arrow_bytes_to_pandas_df = lambda b: pickle.loads(b)
    return True
