import { Component, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { failed: boolean };

// Si una vista falla al mostrarse (p. ej., una respuesta con otra forma), se avisa en lugar de
// dejar la pantalla en blanco. React exige un componente de clase para esto.
export class ViewBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <div role="alert" className="banner banner-error">
          <p>Algo falló al mostrar esta vista. Los datos de la bóveda no se han tocado.</p>
          <button type="button" onClick={() => this.setState({ failed: false })}>
            Reintentar
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
