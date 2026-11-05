module fp32_max_no_idx16 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_value,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_max
);

logic [31:0] running_max;
logic [3:0]  element_count;
logic        busy;

wire input_accepted;
wire output_consumed;
wire [31:0] updated_running_max;
wire produce_output;

function automatic logic is_nan(input logic [31:0] a);
    logic [7:0] exp_a;
    logic [22:0] frac_a;
    begin
        exp_a = a[30:23];
        frac_a = a[22:0];
        is_nan = (exp_a == 8'hFF) && (frac_a != 23'd0);
    end
endfunction

function automatic logic is_zero(input logic [31:0] a);
    begin
        is_zero = (a[30:0] == 31'd0);
    end
endfunction

function automatic logic fp32_lt(input logic [31:0] a, input logic [31:0] b);
    logic a_nan;
    logic b_nan;
    logic a_zero;
    logic b_zero;
    logic a_sign;
    logic b_sign;
    logic [30:0] a_mag;
    logic [30:0] b_mag;
    begin
        a_nan  = is_nan(a);
        b_nan  = is_nan(b);
        a_zero = is_zero(a);
        b_zero = is_zero(b);
        a_sign = a[31];
        b_sign = b[31];
        a_mag  = a[30:0];
        b_mag  = b[30:0];

        fp32_lt = 1'b0;
        if (a_nan || b_nan) begin
            fp32_lt = 1'b0;
        end else if (a_zero && b_zero) begin
            fp32_lt = 1'b0;
        end else if (a_sign != b_sign) begin
            fp32_lt = (a_sign == 1'b1);
        end else if (a_sign == 1'b0) begin
            fp32_lt = (a_mag < b_mag);
        end else begin
            fp32_lt = (a_mag > b_mag);
        end
    end
endfunction

assign input_accepted    = in_valid & in_ready;
assign output_consumed   = out_valid & out_ready;
assign updated_running_max = fp32_lt(running_max, in_value) ? in_value : running_max;
assign produce_output    = (element_count == 4'd15);

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_ff @(posedge clk) begin
    if (rst) begin
        running_max   <= 32'hFF7FFFFF;
        element_count <= 4'd0;
        out_max       <= 32'h00000000;
        busy          <= 1'b0;
    end else if (input_accepted) begin
        if (produce_output) begin
            out_max       <= updated_running_max;
            running_max   <= 32'hFF7FFFFF;
            element_count <= 4'd0;
            busy          <= 1'b1;
        end else begin
            running_max   <= updated_running_max;
            element_count <= element_count + 4'd1;
            busy          <= 1'b0;
        end
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
