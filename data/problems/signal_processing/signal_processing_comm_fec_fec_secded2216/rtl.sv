module signal_processing_comm_fec_fec_secded2216 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_encode,
    input  logic [5:0]  in_parity,
    input  logic [7:0]  in_data0,
    input  logic [7:0]  in_data1,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [5:0]  out_parity,
    output logic [7:0]  out_data0,
    output logic [7:0]  out_data1,
    output logic [1:0]  out_status
);

    logic        busy;
    logic [15:0] message;
    logic [5:0]  parity_calc;
    logic [5:0]  syndrome;
    logic [1:0]  status_calc;
    logic [7:0]  e0;
    logic [7:0]  e1;
    logic [7:0]  e2;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        message = {in_data0, in_data1};

        parity_calc[5] = ^(message & 16'h993C);
        parity_calc[4] = ^(message & 16'h3E8A);
        parity_calc[3] = ^(message & 16'hEE60);
        parity_calc[2] = ^(message & 16'hE1D1);
        parity_calc[1] = ^(message & 16'h13C7);
        parity_calc[0] = ^(message & 16'h443F);

        syndrome[5] = in_parity[5] ^ (^(message & 16'h993C));
        syndrome[4] = in_parity[4] ^ (^(message & 16'h3E8A));
        syndrome[3] = in_parity[3] ^ (^(message & 16'hEE60));
        syndrome[2] = in_parity[2] ^ (^(message & 16'hE1D1));
        syndrome[1] = in_parity[1] ^ (^(message & 16'h13C7));
        syndrome[0] = in_parity[0] ^ (^(message & 16'h443F));

        status_calc = 2'b00;
        e0 = 8'h00;
        e1 = 8'h00;
        e2 = 8'h00;

        if (syndrome == 6'h00) begin
            status_calc = 2'b00;
        end else begin
            unique case (syndrome)
                6'h07: begin status_calc = 2'b01; e2 = 8'h01; end
                6'h13: begin status_calc = 2'b01; e2 = 8'h02; end
                6'h23: begin status_calc = 2'b01; e2 = 8'h04; end
                6'h31: begin status_calc = 2'b01; e2 = 8'h08; end
                6'h25: begin status_calc = 2'b01; e2 = 8'h10; end
                6'h29: begin status_calc = 2'b01; e2 = 8'h20; end
                6'h0E: begin status_calc = 2'b01; e2 = 8'h40; end
                6'h16: begin status_calc = 2'b01; e2 = 8'h80; end
                6'h26: begin status_calc = 2'b01; e1 = 8'h01; end
                6'h1A: begin status_calc = 2'b01; e1 = 8'h02; end
                6'h19: begin status_calc = 2'b01; e1 = 8'h04; end
                6'h38: begin status_calc = 2'b01; e1 = 8'h08; end
                6'h32: begin status_calc = 2'b01; e1 = 8'h10; end
                6'h1C: begin status_calc = 2'b01; e1 = 8'h20; end
                6'h0D: begin status_calc = 2'b01; e1 = 8'h40; end
                6'h2C: begin status_calc = 2'b01; e1 = 8'h80; end
                6'h01: begin status_calc = 2'b01; e0 = 8'h01; end
                6'h02: begin status_calc = 2'b01; e0 = 8'h02; end
                6'h04: begin status_calc = 2'b01; e0 = 8'h04; end
                6'h08: begin status_calc = 2'b01; e0 = 8'h08; end
                6'h10: begin status_calc = 2'b01; e0 = 8'h10; end
                6'h20: begin status_calc = 2'b01; e0 = 8'h20; end
                default: begin status_calc = 2'b10; end
            endcase
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy       <= 1'b0;
            out_parity <= 6'h00;
            out_data0  <= 8'h00;
            out_data1  <= 8'h00;
            out_status <= 2'b00;
        end else if (input_accepted) begin
            if (in_encode) begin
                out_parity <= parity_calc;
                out_data0  <= in_data0;
                out_data1  <= in_data1;
                out_status <= 2'b00;
            end else begin
                out_parity <= in_parity ^ e0[5:0];
                out_data0  <= in_data0 ^ e1;
                out_data1  <= in_data1 ^ e2;
                out_status <= status_calc;
            end
            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule