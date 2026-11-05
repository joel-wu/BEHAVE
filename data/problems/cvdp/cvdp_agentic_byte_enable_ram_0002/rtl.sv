module custom_byte_enable_ram #(
    parameter int XLEN = 32, parameter int LINES = 8192,
    localparam int ADDR_WIDTH = (LINES > 1 ? $clog2(LINES) : 1)
) (
    input logic clk, init_reset,
    input logic [ADDR_WIDTH-1:0] addr_a,
    input logic en_a,
    input logic [XLEN/8-1:0] be_a,
    input logic [XLEN-1:0] data_in_a,
    input logic [ADDR_WIDTH-1:0] addr_b,
    input logic en_b,
    input logic [XLEN/8-1:0] be_b,
    input logic [XLEN-1:0] data_in_b,
    output logic [XLEN-1:0] data_out_a, data_out_b
);
    logic [XLEN-1:0] ram [0:LINES-1];
    logic [ADDR_WIDTH-1:0] addr_a_q, addr_b_q;
    logic en_a_q, en_b_q;
    logic [XLEN/8-1:0] be_a_q, be_b_q;
    logic [XLEN-1:0] data_a_q, data_b_q;
    always_ff @(posedge clk) begin
        if (init_reset) begin
            for (int i=0; i<LINES; i++) ram[i]<=0;
            addr_a_q<=0; addr_b_q<=0; en_a_q<=0; en_b_q<=0;
            be_a_q<=0; be_b_q<=0; data_a_q<=0; data_b_q<=0;
            data_out_a<=0; data_out_b<=0;
        end else begin
            // Read-before-write, from the previously captured addresses.
            data_out_a <= addr_a_q<LINES ? ram[addr_a_q] : '0;
            data_out_b <= addr_b_q<LINES ? ram[addr_b_q] : '0;
            for (int lane=0; lane<XLEN/8; lane++) begin
                if (en_a_q && addr_a_q<LINES && be_a_q[lane])
                    ram[addr_a_q][8*lane +: 8]<=data_a_q[8*lane +: 8];
                if (en_b_q && addr_b_q<LINES && be_b_q[lane] &&
                    !(en_a_q && addr_a_q==addr_b_q && be_a_q[lane]))
                    ram[addr_b_q][8*lane +: 8]<=data_b_q[8*lane +: 8];
            end
            addr_a_q<=addr_a; en_a_q<=en_a; be_a_q<=be_a; data_a_q<=data_in_a;
            addr_b_q<=addr_b; en_b_q<=en_b; be_b_q<=be_b; data_b_q<=data_in_b;
        end
    end
endmodule
