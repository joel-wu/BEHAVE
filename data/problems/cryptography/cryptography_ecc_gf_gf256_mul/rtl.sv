module cryptography_ecc_gf_gf256_mul (
    input  logic       clk,
    input  logic       rst,
    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_a,
    input  logic [7:0] in_b,
    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_p
);

    logic busy;

    wire input_accepted;
    wire output_consumed;

    function automatic logic [7:0] xtime(input logic [7:0] x);
        logic [7:0] a_shift;
        begin
            a_shift = {x[6:0], 1'b0};
            if (x[7]) begin
                xtime = a_shift ^ 8'h1B;
            end else begin
                xtime = a_shift;
            end
        end
    endfunction

    function automatic logic [7:0] gf256_mul(input logic [7:0] a_in, input logic [7:0] b_in);
        logic [7:0] acc;
        logic [7:0] a_work;
        logic [7:0] b_work;
        begin
            acc    = 8'h00;
            a_work = a_in;
            b_work = b_in;

            if (b_work[0]) begin
                acc = acc ^ a_work;
            end
            a_work = xtime(a_work);
            b_work = {1'b0, b_work[7:1]};

            if (b_work[0]) begin
                acc = acc ^ a_work;
            end
            a_work = xtime(a_work);
            b_work = {1'b0, b_work[7:1]};

            if (b_work[0]) begin
                acc = acc ^ a_work;
            end
            a_work = xtime(a_work);
            b_work = {1'b0, b_work[7:1]};

            if (b_work[0]) begin
                acc = acc ^ a_work;
            end
            a_work = xtime(a_work);
            b_work = {1'b0, b_work[7:1]};

            if (b_work[0]) begin
                acc = acc ^ a_work;
            end
            a_work = xtime(a_work);
            b_work = {1'b0, b_work[7:1]};

            if (b_work[0]) begin
                acc = acc ^ a_work;
            end
            a_work = xtime(a_work);
            b_work = {1'b0, b_work[7:1]};

            if (b_work[0]) begin
                acc = acc ^ a_work;
            end
            a_work = xtime(a_work);
            b_work = {1'b0, b_work[7:1]};

            if (b_work[0]) begin
                acc = acc ^ a_work;
            end
            a_work = xtime(a_work);
            b_work = {1'b0, b_work[7:1]};

            gf256_mul = acc;
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy  <= 1'b0;
            out_p <= 8'h00;
        end else if (input_accepted) begin
            busy  <= 1'b1;
            out_p <= gf256_mul(in_a, in_b);
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule