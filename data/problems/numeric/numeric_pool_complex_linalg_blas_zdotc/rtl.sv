module complex_conjugate_dot_product (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [2:0] in_n,
    input logic [3:0] in_incx, in_incy, in_x_base, in_y_base,
    output logic out_valid,
    input logic out_ready,
    output logic [127:0] out_real, out_imag,
    output logic x_mem_req_valid,
    output logic [3:0] x_mem_req_addr,
    input logic x_mem_req_ready, x_mem_resp_valid,
    input logic [127:0] x_mem_resp_rdata,
    output logic y_mem_req_valid,
    output logic [3:0] y_mem_req_addr,
    input logic y_mem_req_ready, y_mem_resp_valid,
    input logic [127:0] y_mem_resp_rdata
);
    typedef enum logic [2:0] {IDLE, X_REQ, X_RESP, Y_REQ, Y_RESP, OUTPUT} state_t;
    state_t state;
    logic [2:0] count, index, accepted_count;
    logic [3:0] x_addr, y_addr, x_stride, y_stride;
    logic signed [63:0] xr, xi, yr, yi;
    logic signed [127:0] rr, ii, ri, ir;
    logic [127:0] acc_real, acc_imag;
    assign accepted_count = in_n > 4 ? 3'd4 : in_n;
    assign in_ready = state == IDLE;
    assign out_valid = state == OUTPUT;
    assign out_real = acc_real;
    assign out_imag = acc_imag;
    assign x_mem_req_valid = state == X_REQ;
    assign y_mem_req_valid = state == Y_REQ;
    assign x_mem_req_addr = x_addr;
    assign y_mem_req_addr = y_addr;
    assign yr = $signed(y_mem_resp_rdata[63:0]);
    assign yi = $signed(y_mem_resp_rdata[127:64]);
    assign rr = xr * yr;
    assign ii = xi * yi;
    assign ri = xr * yi;
    assign ir = xi * yr;
    always_ff @(posedge clk) begin
        if (rst) begin
            state <= IDLE;
            count <= 0; index <= 0;
            x_addr <= 0; y_addr <= 0;
            x_stride <= 0; y_stride <= 0;
            xr <= 0; xi <= 0;
            acc_real <= 0; acc_imag <= 0;
        end else begin
            case (state)
                IDLE: if (in_valid) begin
                    count <= accepted_count;
                    index <= 0;
                    x_stride <= in_incx; y_stride <= in_incy;
                    x_addr <= in_x_base + (in_incx[3] ? (4'd1 - {1'b0, accepted_count}) * in_incx : 4'd0);
                    y_addr <= in_y_base + (in_incy[3] ? (4'd1 - {1'b0, accepted_count}) * in_incy : 4'd0);
                    acc_real <= 0; acc_imag <= 0;
                    state <= accepted_count == 0 ? OUTPUT : X_REQ;
                end
                X_REQ: if (x_mem_req_ready) state <= X_RESP;
                X_RESP: if (x_mem_resp_valid) begin
                    xr <= $signed(x_mem_resp_rdata[63:0]);
                    xi <= $signed(x_mem_resp_rdata[127:64]);
                    state <= Y_REQ;
                end
                Y_REQ: if (y_mem_req_ready) state <= Y_RESP;
                Y_RESP: if (y_mem_resp_valid) begin
                    acc_real <= acc_real + rr + ii;
                    acc_imag <= acc_imag + ri - ir;
                    if (index + 1 == count) state <= OUTPUT;
                    else begin
                        index <= index + 1;
                        x_addr <= x_addr + x_stride;
                        y_addr <= y_addr + y_stride;
                        state <= X_REQ;
                    end
                end
                OUTPUT: if (out_ready) state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end
endmodule
