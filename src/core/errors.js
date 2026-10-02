export class MFlowError extends Error {
  constructor(code, message, status = 400) {
    super(message);
    this.name = 'MFlowError';
    this.code = code;
    this.status = status;
  }
}
