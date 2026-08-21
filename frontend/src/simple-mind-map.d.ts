declare module "simple-mind-map" {
  export default class SimpleMindMap {
    constructor(options: Record<string, unknown>);
    resize(): void;
    destroy(): void;
    view: {
      fit(): void;
      narrow(): void;
      enlarge(): void;
    };
  }
}
