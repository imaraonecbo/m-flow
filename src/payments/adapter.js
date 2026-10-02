export class PaymentRailAdapter {
  constructor(name) { this.name = name; }
  async authorize(_request) { throw new Error('authorize not implemented'); }
  async execute(_request) { throw new Error('execute not implemented'); }
  async status(_reference) { throw new Error('status not implemented'); }
}
