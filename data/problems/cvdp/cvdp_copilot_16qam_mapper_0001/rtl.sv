module qam16_mapper_interpolated #(
    parameter int N = 4, parameter int IN_WIDTH = 4, parameter int OUT_WIDTH = 3
) (
    input logic [N*IN_WIDTH-1:0] bits,
    output logic [(N+N/2)*OUT_WIDTH-1:0] I, Q
);
    function automatic signed [2:0] level(input logic [1:0] code);
        case (code)
            0: level=-3; 1: level=-1; 2: level=1; 3: level=3;
        endcase
    endfunction
    for (genvar pair=0; pair<N/2; pair++) begin: mapping
        wire signed [2:0] i0=level(bits[8*pair+6 +: 2]);
        wire signed [2:0] i1=level(bits[8*pair+2 +: 2]);
        wire signed [2:0] q0=level(bits[8*pair+4 +: 2]);
        wire signed [2:0] q1=level(bits[8*pair +: 2]);
        wire signed [3:0] isum={i0[2],i0}+{i1[2],i1};
        wire signed [3:0] qsum={q0[2],q0}+{q1[2],q1};
        assign I[9*pair+6 +: 3]=i0;
        assign I[9*pair+3 +: 3]=isum >>> 1;
        assign I[9*pair +: 3]=i1;
        assign Q[9*pair+6 +: 3]=q0;
        assign Q[9*pair+3 +: 3]=qsum >>> 1;
        assign Q[9*pair +: 3]=q1;
    end
endmodule
